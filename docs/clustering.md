# 收藏聚类 · 多标签打标

> 把 1000+ 条小红书收藏自动分到 ~12 个簇 + 每条最多 2 个标签。两阶段：LLM 给人能读的标签，e5 找最近邻分配。

代码：[backend/app/services/inbox_clustering.py](../backend/app/services/inbox_clustering.py)。

---

## 1. 数据模型

```
note_inbox.cluster_id          ── 主标签（与 is_primary=1 的那行镜像）
note_cluster_tags(             ── 多标签明细（每条最多 N 行）
  note_id, cluster_id,
  score,                       -- 余弦
  is_primary,                  -- 0/1，每个 note_id 恰好一行 = 1
  source,                      -- 'auto' (currently the only writer)
  created_at,
  PRIMARY KEY (note_id, cluster_id)
  FK → note_inbox.note_id ON DELETE CASCADE
)
```

`note_inbox.cluster_id` 是为了向后兼容保留的冗余字段，所有按簇过滤的写新代码都应该走 `note_cluster_tags`。

---

## 2. 阈值

定义在 `inbox_clustering.py` 顶部：

| 常量 | 默认 | 含义 |
|---|---|---|
| `MAX_TAGS_PER_NOTE` | 2 | 每条笔记的最大标签数 |
| `PRIMARY_MIN_SIMILARITY` | 0.30 | top-1 cosine 的**绝对地板**（很低，主要挡空向量） |
| `PRIMARY_MIN_TOP_GAP` | 0.012 | top-1 与 top-2 的差距，小于此值视为不属于任何簇 |
| `SECONDARY_MIN_SIMILARITY` | 0.48 | 副标签的绝对地板 |
| `SECONDARY_MAX_DELTA_FROM_TOP` | 0.015 | 副标签 cosine 必须落在 top-1 的此差距内 |

**为什么主标签同时要绝对+相对两个判据？** e5 在中文短标题上的余弦分布天然挤在 [0.85, 0.95]：一条与主题无关的合成示例笔记对最近的簇心仍能拿 0.85+ 分，绝对阈值卡不住。**真正的离群信号是 top-1 没显著高于 top-2**——所有簇看起来都差不多近就是"哪都不属于"。

**为什么用 top-1 vs top-2 而不是 top-1 vs mean(其余)？** 当 LLM 产出多个同域簇（例如 6 个旅行类簇），均值会被同域簇心拉高，导致清楚匹配的笔记也被误判为低置信。top-2 只关心真正的次优，是更标准的歧义信号。

两个判据：
- 绝对地板 0.30：兜底，挡空 / 死向量；
- top-1 vs top-2 < 0.012 → 进 `__low_conf__` / `__unclustered__`。

副标签的 0.015 同理：e5 噪音波动在 0.005–0.02 量级，绝对阈值没意义，**真正起约束的是相对差距**。

---

## 2.1 Embedding 输入

`_embed_text(row)` 优先使用 enrichment 写入的 `raw_meta.detail`，把标题、标签和正文开头拼成同一段文本喂给 e5：

```text
<title>
标签: tag1,tag2,tag3
正文: <first ~180 chars>
```

如果 detail 不存在或抓取失败，则回退到 title-only。

为什么不再加 author：当作者字段稀疏时，它可能给主题聚类引入噪音；优先使用标签和正文。具体效果应在获授权的评估数据上验证。

`build_existing_centroids`、`assign_to_existing_clusters`、`cluster_inbox` 的 LLM 出簇/分类路径都用同一个 `_embed_text(row)`，确保增量和全量聚类看到的信息一致。

---

## 3. 两条入口

### 3.1 增量：每次扫描后自动跑（`assign_to_existing_clusters`）

`POST /api/inbox/scan` 抓完列表 → 把"这次新增"的 note_ids 喂给 `assign_to_existing_clusters`。

- 只用 e5（**不调 LLM**），便宜
- 簇心从现有每个簇里 sample 30 条标题取平均
- 算 top-K，按 §2 阈值过滤，写 `note_cluster_tags`
- 任何异常（无 embedding provider、无现有簇、embed 报错）都**不抛出**，把这批 note 全标成 `__unclustered__`，下次「重新聚类」时再扫回来

返回 `auto_cluster: { assigned, unclustered, multi_tagged, by_cluster?, reason? }` 嵌在 scan 响应里。前端 banner 上显示 `· N 条多标签`。

### 3.2 全量：`POST /api/inbox/cluster`（`cluster_inbox`）

跑 LLM 重排所有簇 + 重新打标。流程：

1. 采样 N 条笔记条目（标题 + 标签 + 正文摘要；没有 detail 时 title-only）让 LLM 提议 K 个簇（**强制按主题/活动切，不允许按地理切**——见 [llm.py](../backend/app/providers/llm.py) 的 prompt）
2. 每簇 sample_titles → e5 → 簇心
3. 所有笔记条目 → e5（同 `_embed_text`）→ 对每个簇心算 cosine
4. LLM 按簇菜单给每条笔记分主标签；如果 LLM 大面积返回 none，则回退到 e5 最近簇心 + EM
5. 按 §2 阈值（绝对+相对）处理 e5 fallback；LLM 路径只把 none/未知 id 进 `__low_conf__`
6. `replace_clusters()` + `set_note_tags()`

> **为什么 prompt 禁地理簇？** 以假设的咖啡笔记为例，按国家切分可能让地点盖过活动主题。prompt 因此要求按**做什么/看什么**切（户外/咖啡/穿搭/数码…），最多保留 1 个"旅行/出行"宽桶。这是分类目标的约束，不代表任何真实收藏记录。

关键防御：

- **空 proposal 提前 return** — LLM/fallback 给空数组时直接退出，否则 `replace_clusters([])` 会级联清掉所有 `note_cluster_tags`
- **先 replace_clusters 再 set_note_tags** — replace_clusters 的 dangling-tag 清理只删不在新簇里的 tag，所以新写的 tag 不会被自己删

---

## 4. 兜底簇

| ID | 名称 | 来源 |
|---|---|---|
| `__unclustered__` | 未聚类 | scan 后 `assign_to_existing_clusters` 兜底（无 provider / 无现有簇 / 低置信度 / 异常） |
| `__low_conf__` | 其他（低置信度） | 全量 `cluster_inbox` 里 top-1 < `min_similarity` |

两者都是「把笔记留下，等下一次全量聚类把它们扫进真正的簇」的占位。

---

## 5. 前端表现

- `/inbox` 卡片底部：`tags[1..]` 渲染成小灰 chip，主标签是粉色 chip
- `?cluster=X` 过滤走 `list_inbox_by_cluster_any` — **任意标签匹配**，所以一条笔记会同时出现在多个簇的视图里
- scan banner：`本次见到 N · 新增 X · 更新 Y · M 条多标签 · 命中已知笔记后提前结束`

---

## 6. 一次性回填

历史数据（多标签 schema 上线前的旧 inbox）：

```bash
cd backend && python -m scripts.backfill_multi_tags
```

幂等。重跑就是按当前簇心重新打标。
