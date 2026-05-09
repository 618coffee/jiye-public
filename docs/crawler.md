# 小红书爬虫工作流（crawler workflow）

把 1000+ 条小红书收藏一次性导进知识库的全流程：抓列表 → 聚类打标 → 人工筛选 → 升级入库。
后续新收藏走 webhook 流（见 §6）。

---

## 0. 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│  调试 Chrome (--remote-debugging-port=9222)  ← 一次性登录小红书    │
│  ┌─────────────────────────┐                                     │
│  │  xiaohongshu.com/user/  │ ◀── A. List harvester (Playwright)  │
│  │  profile/<uid>?tab=fav  │     滚动收藏页，抓 note_id +         │
│  │                         │     xsec_token + 标题 + 封面         │
│  └─────────────────────────┘                                     │
└──────────────────────────────────────────────────────────────────┘
               │  写入 note_inbox（pending）
               ▼
       ┌─────────────────┐
       │  inbox_clusters │  LLM 打标 + e5 最近邻聚类 → ~12 个簇
       └─────────────────┘
               │  人工在 /inbox 页面多选
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  B. Detail HTTP fetcher（无 cookie）                              │
│  GET https://www.xiaohongshu.com/explore/<id>?xsec_token=...     │
│  解析 <script>window.__INITIAL_STATE__ = {...}</script>          │
│  → title / body / images / tags / publish time                   │
└──────────────────────────────────────────────────────────────────┘
               │  ingest_note() 走 LLM 摘要 + POI 抽取 + 嵌入
               ▼
       ┌──────────┐
       │  notes   │  主库
       └──────────┘
```

**关键设计**：

- **List 用 Playwright/CDP**：xhs 收藏页是登录态 SPA，cookie 必带。CDP 附着在你的真实 Chrome 上是风控最不敏感的姿势。
- **Detail 用 cookie-less HTTP**（route B，源自 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 思路）：xhs 的 `/explore/<id>` 端点对**无 cookie 请求**返回完整 SSR HTML（含 `__INITIAL_STATE__` 全量笔记 JSON）；带 cookie 反而触发风控（弹二维码扫码墙）。
- **xsec_token 是必需的**：URL 里 `xsec_token=...&xsec_source=pc_user` 由 list 阶段从 `.cover` anchor 上抓下来，存进 `note_inbox.raw_meta`，detail 阶段直接用。token 几小时一过期，过期就重扫一遍 list。
- **camelCase**：xhs SSR 用 `imageList`/`tagList`/`time`/`lastUpdateTime`/`urlDefault`；signed API 才用 snake_case。我们两个都接。

---

## 1. 一次性准备

### 1.1 启动后端

```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload    # 端口 8000
```

第一次跑会自动建表（`note_inbox` / `inbox_clusters`）。

### 1.2 启动调试 Chrome（**不要** ⌘Q 你日常 Chrome；用隔离 profile）

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/xhs-crawler-profile" \
    --no-first-run
```

新窗口里登录小红书一次（profile 持久化在 `~/xhs-crawler-profile`）。验证：

```bash
curl -s localhost:9222/json/version | head -3
```

### 1.3 配置环境变量（可选）

`backend/.env`：

```bash
XHS_CDP_ENDPOINT=http://127.0.0.1:9222
# 下面这条可选 — 不填爬虫会自动发现
XHS_COLLECTION_URL=https://www.xiaohongshu.com/user/profile/<你的uid>?tab=fav&subTab=note
```

**`XHS_COLLECTION_URL` 是可选的**（MediaCrawler 风格的三级 fallback）：

1. 如果调试 Chrome 里有 tab 的 URL 已经像收藏页（带 `tab=fav` / `subTab=note`），直接用
2. 否则找一个 `/user/profile/<uid>` 的 tab，自动拼上 `?tab=fav&subTab=note` 跳转过去
3. 都没有 → 在第一个 xhs tab 里读 DOM（header 头像里的 `/user/profile/<uid>` 链接）拿到 uid，自己拼 URL

所以**最简单的玩法**：在调试 Chrome 里随便打开一个 xhs 页面（首页 / 任意笔记都行），登录，直接点「扫描收藏夹」，爬虫自己找路。

---

## 2. Stage 1 — 抓收藏列表

### 2.1 一键抓（推荐，UI）

打开 [http://localhost:3000/inbox](http://localhost:3000/inbox)（页面标题「我的收藏」），点 header 上的 **「扫描收藏夹」** 按钮。

走 `POST /api/inbox/scan`。`XHS_COLLECTION_URL` 没设的话用 §1.3 的 fallback 自动发现。

**每次扫描都会强制刷新 feed**：爬虫执行 `scrollTo(0)` → `page.reload()` → 重新点击「收藏」tab → `scrollTo(0)`，然后才开始滚。这样 tab 闲置期间新收藏的笔记也能被吃到（xhs 是 SPA，不主动 reload 永远看的是缓存的 feed），mirror MediaCrawler 的「每次新鲜导航」行为。

**默认是增量扫描**：xhs 收藏页按收藏时间倒序，爬虫拿到 `note_inbox` 里所有已知 `note_id` 的集合（`known_note_ids`），扫到连续 `stop_known_streak=2` 屏全是已知笔记就提前停。所以平时定期点这个按钮，只会抓最近新增的几十条，几秒就跑完。

**全量扫描**：按住 `Shift` 点击按钮（或 `POST /api/inbox/scan` 带 `{"full": true}`），关闭增量逻辑，从头滚到底。用于：
- 第一次冷启动（其实空 inbox 时会自动走全量）
- 怀疑漏掉了笔记（比如手动取消过收藏后重新收藏）
- xhs 改版后想重抓 xsec_token

**Scan 完会自动跑增量聚类**（`assign_to_existing_clusters`）：把这次新增的 note_id 用 e5 配到最近的现有簇，写 `note_cluster_tags`（多标签）。详见 [docs/clustering.md](./clustering.md)。响应里返回 `auto_cluster: { assigned, unclustered, multi_tagged, by_cluster?, reason? }`。

banner 提示：「本次见到 N · 新增 X · 更新 Y · M 条多标签 · 命中已知笔记后提前结束」。

### 2.2 CLI（高级用法 / 调试）

```bash
cd backend && source .venv/bin/activate

# 试跑（不写库）— 不带 --url 走自动发现
python -m app.crawler.cli scan_inbox --dry-run --debug

# 正式 — 强制走某个 URL
python -m app.crawler.cli scan_inbox \
  --url "https://www.xiaohongshu.com/user/profile/<uid>?tab=fav&subTab=note"
```

参数：

| flag | 默认 | 说明 |
|---|---|---|
| `--cdp` | `http://127.0.0.1:9222` | Chrome 调试端点 |
| `--url` | (auto) | 强制 navigate 到这个 URL；不传走 §1.3 自动发现 |
| `--idle` | `4` | 连续 N 次滚动无新内容就停 |
| `--pause` | `1.4` | 每次滚动后停顿（秒），含随机 jitter |
| `--max-scrolls` | `400` | 滚动次数硬上限 |
| `--debug` | off | 打印每次滚动新增数 + tab 选择决策 |
| `--dry-run` | off | 只打印不写库 |

CLI 目前**不支持**增量模式（不会读 `known_note_ids`），每次跑都是全量。增量逻辑只在 HTTP API 层。

### 2.3 重复抓是安全的

`upsert_inbox_many` 是 idempotent：

- 同 `note_id` 不会重复插
- `discarded` 不会被重置回 `pending`（soft delete sticky）
- `promoted` 不会回退
- `xsec_token` 每次都刷新（之前的可能已过期）

### 2.4 数据落点

- `data/index.db` → SQLite
- `note_inbox` 表：`note_id, url, title, cover_url, author, status, cluster_id, raw_meta, discovered_at, saved_at`
- `note_inbox.raw_meta`：JSON，含 `xsec_token` / `xsec_source` / `discovered_via` / `page_url`

```bash
sqlite3 data/index.db "SELECT status, COUNT(*) FROM note_inbox GROUP BY status;"
curl localhost:8000/api/inbox/stats | python -m json.tool
```

---

## 3. Stage 2 — 聚类打标

> 多标签逻辑、阈值、藏底簇详见 [docs/clustering.md](./clustering.md)。这里只讲触发路径。

### 3.1 触发（UI）

`/inbox` header → **「重新聚类」** 按钮，弹出表单设置目标簇数 / 样本量 / EM 迭代。

### 3.2 触发（API）

```bash
curl -X POST localhost:8000/api/inbox/cluster \
  -H 'Content-Type: application/json' \
  -d '{"target_clusters": 12, "sample_size": 200}'
```

参数：

- `target_clusters` — LLM 提议的簇数（推荐 8-15）
- `sample_size` — LLM 看到的样本量（150-300）
- `status`（默认 `pending`）+ `only_unassigned`（增量聚类）
- `em_iterations` — 0/1/2/3 轮 EM 重分配（推荐 1）
- `min_similarity` — 低置信度阈值（推荐 0.55）

### 3.3 算法（两阶段 + 多标签）

1. **LLM 出标签**：从样本里采 N 条标题让 LLM 命名 K 个簇（人能读的中文标签 + 描述）。
2. **e5 找最近邻**：所有标题 + 簇描述都用 `multilingual-e5-base` 编码，对每个簇心算 cosine。
3. **EM 收敛**（可选）：用簇内标题平均向量重算簇心，再分配，迭代 1-3 次。
4. **多标签打标**：top-1 进主标签；后续候选中满足「与 top-1 cosine 差距 ≤ 0.015 且绝对值 ≥ 0.48」的进副标签，每条最多 2 个。
5. **低置信度回退**：max sim < `min_similarity` 的判为「未分类」（`__low_conf__`）。

耗时取决于数据规模和所选 provider。簇 + 时间聚合（earliest/latest discovered_at + saved_at + 近 7 日新增数）写入 `inbox_clusters`；per-note 的多标签写入 `note_cluster_tags`。

### 3.4 阈值建议

- **1000 条以下**：`target_clusters=8`，`sample_size=150`
- **1000-3000 条**：`target_clusters=12`，`sample_size=250`
- **3000+**：分批跑，每批 `only_unassigned=true`

---

## 4. Stage 3 — 人工筛选 + 升级入库

### 4.1 筛选（UI）

`/inbox` 页：

- 左侧簇 sidebar，按笔记数倒序，每个簇展示「最近活跃 / 近 7 天 N」时间 chip
- 右侧网格：状态平铺胶囊（待处理 / 已入库 / 已丢弃 / 全部），标题搜索，多选
- 卡片右下角显示「N 天前发现」chip
- 已入库卡点击 → 跳 `/note/<id>` 看主库正文

### 4.2 升级（UI）

多选 → header 操作栏 **「升级到主库」** 按钮 → `POST /api/inbox/promote`。

每批最多 50 条（`max_per_request`），前端按 10 条切片串行跑，避免超时 + 给 progress。已 `promoted` 的笔记自动剔除（防呆）。

### 4.3 升级管道（HTTP-first / SPA-fallback）

`backend/app/services/inbox_promote.py`：

- **Phase 1（HTTP 优先）**：遍历选中的 `pending`，有 `xsec_token` 就调 `fetch_note_detail_http(note_id, xsec_token, xsec_source)`：
  - cookie-less GET `https://www.xiaohongshu.com/explore/<id>?xsec_token=...&xsec_source=pc_user`
  - 正则提取 `window.__INITIAL_STATE__ = {...}`，先把裸 `undefined` 替换成 `null`，再 `json.loads`
  - 走 `noteData.noteDetailMap[<id>].note` → title/body/images/tags
  - `time` (ms) → ISO publish 时间戳
  - 检测 QR 墙关键词（"请打开小红书 App 扫码查看" 等）→ 失败回落
  - 节流 0.6-1.4s
- **Phase 2（SPA 兜底）**：HTTP 失败的笔记走旧的 `XhsDetailCrawler`（Playwright 在调试 Chrome 里 click `.cover` 弹模态框，从 DOM 抓内容）。
- 拿到 `DetailResult` → `ingest_note()` → LLM 摘要 + POI 抽取 + 向量嵌入 → 写 `notes` / `note_chunks` / `note_poi`
- `note_inbox.status` 设为 `promoted`，`promoted_at` 写入

### 4.4 探针逻辑（probe）

`detail.py:_DETAIL_PROBE_JS` 防止「点了但模态框没打开」就误以为成功：

- 必须有 `#noteContainer` 或 `[class*="noteDetailMask"]`（**强模态信号**）
- AND（正文 ≥ 8 字符 OR 抓到 publish 时间）OR（强模态 + ≥2 张图 + 作者）

`published_at` 抽取支持：`YYYY-MM-DD` / `M-D` / `编辑于 ...` / `N 天/周/月/年前` / `昨天` / `今天` / `刚刚`。

---

## 5. 故障排查

| 症状 | 解决 |
|---|---|
| `Could not attach to Chrome at 127.0.0.1:9222` | Chrome 没用 `--remote-debugging-port=9222` 启动，按 §1.2 重启 |
| `No xiaohongshu tab found` | 调试 Chrome 里没开任何 xhs 页面，随便打开 xhs.com 并登录 |
| `Could not auto-locate your xhs collection page` | 有 xhs tab 但都不是 profile/收藏，xhs header 也读不到 uid。手动跳到自己 profile 后重试，或在 `XHS_COLLECTION_URL` 里写死 |
| 抓回来全是别人发的笔记 | 你停在了 `/explore`，用 `--url` 直达 profile；或者在调试 Chrome 里手动跳到你的 profile 页让自动发现走途径 2/3 |
| 抓回来全是自己发的笔记 | 收藏 tab 没切换，加 `--debug` 看 `tab click attempt` 是否 `clicked=true` |
| 数量比预期少很多 | 加 `--idle 8 --max-scrolls 800 --pause 2.0` 重跑 |
| Promote 时弹二维码扫码墙 | 不该再发生（HTTP path 是无 cookie 的）。如果出现，说明落到 SPA fallback 了 — 检查 token 是否丢失（重新 scan 刷 token） |
| HTTP path 报 "note_card missing from __INITIAL_STATE__" | xsec_token 失效或 note 已被作者删除。重 scan，或落到 SPA fallback |
| 想从头来过 | `rm data/index.db` 重启后端会自动建（**会丢光所有数据**） |

---

## 6. Stage 4 — webhook + iOS Shortcut（待实现）

- `POST /api/ingest/share` 接受 `{url, token}`
- 通过 Tailscale Funnel 把 Mac 暴露到公网
- iOS Shortcut：分享小红书帖子 → 触发 Shortcut → POST 到 webhook → 走同样 detail HTTP fetcher

---

## 7. 相关代码 / 参考

**本仓库**：

- 列表抓取：[backend/app/crawler/xhs.py](../backend/app/crawler/xhs.py)
- HTTP 详情：[backend/app/crawler/detail_http.py](../backend/app/crawler/detail_http.py)
- SPA 详情兜底：[backend/app/crawler/detail.py](../backend/app/crawler/detail.py)
- 升级管道：[backend/app/services/inbox_promote.py](../backend/app/services/inbox_promote.py)
- 聚类：[backend/app/services/inbox_clustering.py](../backend/app/services/inbox_clustering.py)（详见 [docs/clustering.md](./clustering.md)）
- API：[backend/app/routers/inbox.py](../backend/app/routers/inbox.py)
- CLI：[backend/app/crawler/cli.py](../backend/app/crawler/cli.py)
- UI：[frontend/app/inbox/page.tsx](../frontend/app/inbox/page.tsx)

**外部参考（Route B 思路来源）**：

- [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) — 多平台爬虫，xhs 部分演示了 cookie-less SSR 路径
- [ReaJason/xhs](https://github.com/ReaJason/xhs) — 纯 Python xhs SDK
- [xhshow](https://github.com/btn-pwn/xhshow) — xhs 签名/接口逆向

---

## 8. 设计小记

- **不用 headless**：CDP 附着真实登录 Chrome，是 xhs 风控最不敏感的姿势
- **List-only Playwright**：列表阶段不进详情页，把"风险"留给慢速 promote
- **Cookie-less detail**：xhs 的反爬是基于 cookie 行为画像 + click `isTrusted` 检测，无 cookie SSR 是它给搜索引擎留的口子，正好被我们吃掉
- **Soft delete sticky**：`discarded` 状态不会被 upsert 覆盖
- **二阶段聚类**：LLM 给标签（人能读）+ e5 找最近邻（成本可控）
