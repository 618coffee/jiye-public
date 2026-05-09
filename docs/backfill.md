# 收藏夹回填工作流（backfill workflow）

> 本文所有命令输出均为合成示例，不对应真实账号、收藏数量或访问记录。

把存量的 1000+ 条小红书收藏一次性导进知识库，之后每次新收藏走 webhook 流。
全流程分 5 个阶段：抓列表 → 聚类打标 → 人工筛选 → 深度处理 → 上线 webhook。
本文档目前覆盖 **Stage 1（爬虫）**+ **Stage 2a（聚类）**，其余 stage 后续补。

---

## 0. 前置准备（一次性）

### 0.1 启动后端

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload      # 另开一个终端，端口 8000
```

第一次跑会自动建表（包含 `note_inbox` / `inbox_clusters`）。

### 0.2 启动一个带远程调试端口的 Chrome

> **不要** ⌘Q 你日常那个 Chrome —— 用一个隔离的 profile 即可，互不干扰。

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/xhs-crawler-profile" \
    --no-first-run
```

新窗口出现后：
1. 在这个新窗口里登录小红书（**只登一次**，profile 持久化在 `~/xhs-crawler-profile`）
2. 验证调试端口活着：
   ```bash
   curl -s localhost:9222/json/version | head -3
   ```

> 之后再跑爬虫，重复执行上面那条 Chrome 命令即可，不需要重新登录。

---

## 1. Stage 1 — 抓收藏列表

### 1.1 试跑（dry-run，不写库）

```bash
cd backend && source .venv/bin/activate
python -m app.crawler.cli scan_inbox --dry-run --debug \
  --url "https://www.xiaohongshu.com/user/profile/<你的uid>?tab=fav&subTab=note"
```

`--url` 会让爬虫主动 navigate 到收藏页 + 自动 click「收藏」tab（XHS 的 tab 状态藏在 JS 里，URL 看不出来，所以必须爬虫主动点一下）。

预期输出：
```
[crawler] initial probe found 12 items on https://www.xiaohongshu.com/...
[crawler] iter 0: +18 (total 30)
...
[crawler] 4 idle scrolls — done
[crawler] done. 1000 unique notes from https://www.xiaohongshu.com/...
[crawler] dry-run; first 5 items:
  64a1b2c3...  京都祇园抹茶圣代必吃
  ...
```

如果数量明显偏少：
- 加大 idle 容忍：`--idle 8`
- 加大滚动上限：`--max-scrolls 800`
- 放慢节奏（更稳，但慢）：`--pause 2.0`

### 1.2 正式跑（写库）

把 `--dry-run` 去掉就行：

```bash
python -m app.crawler.cli scan_inbox \
  --url "https://www.xiaohongshu.com/user/profile/<你的uid>?tab=fav&subTab=note"
```

输出会变成：
```
[crawler] db: inserted=1000 updated=0
[crawler] inbox status counts: {'pending': 1000}
```

**重复跑是安全的** —— `upsert_inbox_many` 是 idempotent 的：
- 同 `note_id` 不会重复插
- 已经被你 `discarded` 的不会被重置回 `pending`（soft delete sticky）
- 已经 `promoted` 的也不会回退

### 1.3 数据落在哪

| 位置 | 内容 |
|---|---|
| `backend/data/index.db` | SQLite 主库 |
| `note_inbox` 表 | 每条收藏一行：note_id, url, title, cover_url, author, status, cluster_id, raw_meta |
| `inbox_clusters` 表 | Stage 2a 才会填 |

### 1.4 直接看数据

```bash
# 命令行
sqlite3 backend/data/index.db \
  "SELECT note_id, title FROM note_inbox LIMIT 10;"
sqlite3 backend/data/index.db \
  "SELECT status, COUNT(*) FROM note_inbox GROUP BY status;"

# 或者用 API（需要后端在跑）
curl localhost:8000/api/inbox/stats | python -m json.tool
curl 'localhost:8000/api/inbox?limit=5' | python -m json.tool
```

### 1.5 CLI 选项速查

| flag | 默认 | 说明 |
|---|---|---|
| `--cdp` | `http://127.0.0.1:9222` | Chrome 调试端点 |
| `--url` | (空) | 强制 navigate 到这个 URL（推荐用） |
| `--idle` | `4` | 连续 N 次滚动无新内容就停 |
| `--pause` | `1.4` | 每次滚动后基础停顿（秒），会再加随机 jitter |
| `--max-scrolls` | `400` | 滚动次数硬上限 |
| `--debug` | off | 打印每次滚动新增数 |
| `--dry-run` | off | 只打印不写库 |

---

## 2. Stage 2a — 自动聚类

把 inbox 里的 1000+ 条标题用「LLM 出标签 + e5 找最近邻」二阶段算法分到约 12 个簇里，方便你按主题批量决定要不要深度处理。

### 2.1 触发聚类

```bash
curl -X POST localhost:8000/api/inbox/cluster \
  -H 'Content-Type: application/json' \
  -d '{"target_clusters": 12, "sample_size": 200}'
```

参数：
- `target_clusters` — LLM 提议的簇数（推荐 8-15）
- `sample_size` — LLM 看到的样本量（推荐 150-300，越大成本越高）
- `status` — 默认 `pending`，只对未处理的做聚类
- `only_unassigned` — `true` 时跳过已经有 cluster_id 的（增量聚类用）

返回：
```json
{
  "clusters": [
    {"id": "food-jp", "name": "日本美食", "description": "...", "count": 187},
    {"id": "cafe-sh", "name": "上海咖啡", "description": "...", "count": 94},
    ...
  ],
  "assigned": 1000,
  "skipped": 0,
  "duration_s": 12.3
}
```

### 2.2 查看簇

```bash
curl localhost:8000/api/inbox/clusters | python -m json.tool
```

按某个簇过滤 inbox：

```bash
curl 'localhost:8000/api/inbox?cluster_id=food-jp&limit=20' | python -m json.tool
```

### 2.3 怎么决定阈值

- **1000 条以下**：`target_clusters=8`，`sample_size=150` 即可
- **1000-3000 条**：`target_clusters=12`，`sample_size=250`
- **3000+**：考虑分批跑，每批 `only_unassigned=true`

聚类完全可重跑，旧簇会被覆盖。

### 2.4 离线 / 没配 LLM 的情况

如果没配 Azure Foundry / Qwen，会自动 fallback 到 mock provider，依然能给出基于关键词的粗糙簇（用于功能验证）。生产建议接 LLM。

---

## 3. Stage 2b — 筛选 UI（待实现）

下一步会做 `/inbox` 前端页：
- 状态筛选 + 日期范围 + 标题搜索
- 簇 card 网格
- 缩略图 grid + 多选
- 「丢弃 N 条」「处理选中 N 条」按钮

API 已经齐了，前端缺。

---

## 4. Stage 3 — 选中深度处理（待实现）

把用户多选的 note_id 列表交给现有 `ingest_note()` pipeline：抓详情 + OCR + LLM 抽取 + 嵌入。
速率限制 3-5 条/分钟，进度通过 SSE 或轮询 `/api/inbox/promote/status` 推送。

---

## 5. Stage 4 — webhook + iOS Shortcut（待实现）

- `POST /api/ingest/share` 接受 `{url, token}`
- 通过 Tailscale Funnel 把 Mac 暴露到公网
- iOS Shortcut 模板：分享小红书帖子 → 触发 Shortcut → POST 到 webhook

---

## 6. 故障排查

| 症状 | 解决 |
|---|---|
| `Could not attach to Chrome at 127.0.0.1:9222` | Chrome 没用 `--remote-debugging-port=9222` 启动，按 0.2 重启 |
| `No xiaohongshu tab found` | 在调试 Chrome 里打开任意 xhs 页 |
| `Found xiaohongshu tabs but none look like a 收藏 page` | 用 `--url` 强制指定收藏页 URL |
| 抓回来全是别人发的笔记/推荐流 | 你停在了 `/explore`，用 `--url` 直达 profile |
| 抓回来全是自己发的笔记 | 收藏 tab 没切换成功，加 `--debug` 看 `tab click attempt` 是否 `clicked=true`，必要时手动点一下 tab 再跑 |
| 数量比预期少很多 | 加 `--idle 8 --max-scrolls 800 --pause 2.0` 重跑 |
| 想从头来过 | `rm backend/data/index.db` 然后重启后端会自动重建（**会丢光所有数据**，慎用） |

---

## 7. 设计小记

- **不用 headless** —— CDP 附着在你的真实登录 Chrome 上，是 XHS 风控最不敏感的姿势
- **list-only 抓取** —— 本 stage 完全不进详情页，把"风险"留给后续慢速 promote 流程
- **soft delete sticky** —— 你 discard 过的笔记，重跑爬虫不会复活（详见 `note_inbox.status` 在 upsert 时不被覆盖）
- **二阶段聚类** —— LLM 给标签（人能读）+ e5 找最近邻（成本可控），耗时取决于数据规模和所选 provider
