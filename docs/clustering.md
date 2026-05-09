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
| `PRIMARY_MIN_SIMILARITY` | 0.55 | top-1 低于此阈值 → 整条笔记进 `__unclustered__`，单标签 |
| `SECONDARY_MIN_SIMILARITY` | 0.48 | 副标签的绝对地板 |
| `SECONDARY_MAX_DELTA_FROM_TOP` | 0.015 | 副标签 cosine 必须落在 top-1 的此差距内 |

**为什么是 0.015 这么紧？** e5 在中文短标题上的余弦分布天然挤在 [0.85, 0.95]（同一篇 README §4 的 e5 噪音问题），绝对地板基本没用，**真正起约束的是相对差距**。0.015 是待评估的启发式阈值，应使用合成或获授权的数据验证副标签质量。

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

1. 采样 N 条标题让 LLM 提议 K 个簇（人能读的中文标签 + 描述 + 每簇 3 个示例）
2. 每簇 sample_titles → e5 → 簇心
3. 所有标题 → e5 → 对每个簇心算 cosine
4. 可选 EM 1–3 轮重算簇心（需要簇里 ≥3 条才更新）
5. 按 §2 阈值打主+副标签，低于 `min_similarity` 的进 `__low_conf__`
6. `replace_clusters()` + `set_note_tags()`

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
