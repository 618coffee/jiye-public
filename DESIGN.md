# 小红书个人收藏 AI 知识库 — 设计与可行性方案

> 版本 v3.0 · 2026-05-09  
> 目标：把"小红书收藏夹"变成一个可用自然语言检索的、随时间持续验证的个人旅行/美食/生活知识库。  
> v3 变更：**国内市场优先**，云端全栈切换到阿里云；澄清"双数据库（SQLite/Postgres）"的取舍逻辑；新增海外 API 跨境出口方案、ICP/算法备案策略、国内云月度成本表。

---

## 0. TL;DR（如果只看一段）

- **形态**：**双形态发布** — ① 云端 SaaS（你维护，**国内市场优先**，多租户，手机即开即用） ② 自部署版（开源，单机 SQLite，数据 100% 在用户家里）。两形态共享一套核心代码 + 业务逻辑，差别只在存储后端。
- **数据获取**：用 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（Playwright 复用网页登录态）做主链路，**+ iOS 原生 Share Extension** 做"实时入库"补充。不用 App 抓包，不用 mitmproxy，不值得。
- **AI 索引（分级）**：**文本默认全开**（必跑：摘要 + 结构化抽取 + 城市分类 + embedding）；**图片可选**：免费档只 OCR + 缩略图存原图链接，付费档才跑 Qwen-VL-Max 多模态描述。用户能随时升级单条帖子或单个城市的索引深度。
- **存储**：自部署版 = SQLite + sqlite-vec + FTS5（一个文件，零依赖）；**云端 = 阿里云 PolarDB-PG + pgvector + zhparser 一锅端 + OSS 存原图 + 阿里云百炼跑 LLM**，一个 region 内网闭环。两种数据库通过 `KnowledgeStore` 接口屏蔽（见 §3 决策 4.5）。
- **海外 API 跨境**：阿里云 CEN + 香港 region 函数计算做出口，调 Google Places / OpenAI 合规 + 稳定，单查询 100–150ms。MVP 阶段先用按量 GA 省钱。
- **鲜活度验证**：所有帖子入库时被 LLM 自动打 `city` 标签；用户在 UI 选定一个城市才触发该城市 POI 的高德/Google 验证 — 收藏夹里有 LA / 东京 / 广州，下个月去东京就只刷东京。
- **UX 主入口**：不是一个搜索框，而是一个 **"目的地工作台"** — 输入"6 月去京都"，自动聚合所有相关收藏，按"必去 / 美食 / 咖啡 / 购物 / 拍照"成卡片，每张卡片带鲜活度徽章和地图。
- **成本**：自部署用户 ± ¥10/月（用户自付 LLM）；云端 SaaS 单用户**边际 ¥3–8/月**，定价 ¥19/月 Pro / ¥39/月 Pro+，**30 个付费用户即可覆盖 MVP 月度基础设施 ¥525**。

---

## 1. 你真正要解决的问题

把你的需求翻译成产品语言，是 4 件事，不是 1 件：

| # | 痛点 | 当前小红书做不到 | 这个方案解法 |
|---|------|---------|---------|
| 1 | 收藏太多翻不动 | 收藏夹只有时间倒序+文件夹 | 多模态索引 + 元数据切面 |
| 2 | 想去某地，记不起当初为啥收藏 | 搜索是全平台搜，不是搜"我的收藏" | 按 POI 聚合的"目的地工作台" |
| 3 | 老帖子推荐的地方可能塌房了 | 完全不验证 | POI 鲜活度评分 + 最近评论关键词 |
| 4 | 收藏完就忘了 | 没有任何回顾机制 | "出行前推送" + "周回顾"被动提醒 |

**关键设计原则**：双形态共生，**核心业务逻辑必须可抽象成 storage-agnostic 的接口层**（写一个 `KnowledgeStore` trait/interface，自部署是 SQLite 实现，云端是 Turbopuffer + Postgres 实现）。否则维护两套代码会拖死一个人的项目。

---

## 1.5 部署形态对比（重要）

| 维度 | ☁️ 云端 SaaS | 💻 自部署 |
|------|-----------|---------|
| 谁运维 | 你 | 用户自己 |
| 数据在哪 | 你的云（用户可导出/删除） | 用户电脑/NAS |
| 手机怎么用 | 直接打开 PWA / 原生 App，秒开 | 需 Tailscale Funnel 隧道，技术门槛中等 |
| 小红书 cookie | 用户上传到云，envelope 加密 | 永远不离开本机 |
| LLM API key | 平台代付（捆绑订阅） or 用户自带 | 用户自带 |
| 起步成本 | 用户：¥19/月订阅；你：¥80–150/月基础设施 | 用户：¥0 + LLM API 自付 |
| 适合人群 | 80% 普通用户、不愿折腾、要随时手机刷 | 极客 / 隐私敏感 / 长期使用希望摊薄成本 |
| 数据隔离 | Postgres RLS + Turbopuffer namespace | 物理隔离（各自一台机） |
| 商业化空间 | 订阅 / 增值（图片深度索引、跨设备同步、行程导出） | 开源拉口碑 |

**两形态的关键差异在 3 点**：① 存储后端（SQLite ↔ Turbopuffer+Postgres+S3）② 认证（无 ↔ Supabase Auth）③ 多租户字段（无 ↔ 所有表带 `tenant_id`）。其他全都共享。

**推荐发布节奏**：
1. 先开源自部署版本拉口碑、验证产品价值（Phase 1–2）
2. 同期把 storage 接口抽象好
3. Phase 3 上云端 SaaS，定价 ¥19/月（基础）/ ¥39/月（含图片深度索引）
4. 自部署用户不流失 — 他们是社区贡献者和长期口碑来源

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│  采集层                                                            │
│  ┌──────────────────────┐    ┌────────────────────────────────┐  │
│  │ MediaCrawler         │    │ iOS Shortcut → 自建 webhook    │  │
│  │ 周期性扫"我的收藏"    │    │ 用户点"分享" → 立即入库         │  │
│  │ (主链路, 全量)        │    │ (实时, 补充)                   │  │
│  └──────────┬───────────┘    └──────────┬─────────────────────┘  │
│             └──────────────┬─────────────┘                       │
└────────────────────────────┼──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  原始内容池 (raw blob: 文本 / 图片文件 / 视频封面)                 │
│  存储：~/xhs-kb/raw/{note_id}/                                    │
└────────────────────────────┬──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  AI 处理流水线 (异步队列)                                         │
│   1. PaddleOCR  ─── 图片内文字 (菜单/价格/路标)                    │
│   2. Qwen-VL-Max  ── 结构化描述 + 分类 (景点/餐厅/咖啡/购物/打卡)  │
│   3. Qwen JSON-Mode ── 抽取 {city, poi, type, must_try, price...} │
│   4. BGE-M3 (本地) ── 文本 embedding                              │
└────────────────────────────┬──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  索引存储  ──  SQLite 单文件 (~/xhs-kb/index.db)                  │
│  ├ notes (id, url, raw_text, saved_at, …)                         │
│  ├ pois  (poi_id, name, city, country, type, lat, lng, …)         │
│  ├ note_poi (M:N) + 抽取出的 must_try / price / 评价               │
│  ├ note_chunks_vec  (sqlite-vec, BGE-M3, 1024d)                   │
│  ├ note_chunks_fts  (FTS5 BM25)                                   │
│  └ poi_freshness (status, score, last_checked_at, signals_json)   │
└────────────────────────────┬──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  鲜活度验证 (按需触发, 不是定时全量)                               │
│  国内: 高德 POI API + 小红书最近 60 天反查 + 评论关键词            │
│  海外: Google Places (business_status / 最新评论)                 │
└────────────────────────────┬──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  前端 (Next.js / 或 SwiftUI iOS App)                              │
│  ① 目的地工作台   ② 自然语言搜索   ③ 收藏回顾   ④ 单帖详情        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. 关键技术决策（带"为什么"）

### 决策 1：数据采集 = MediaCrawler 主链路 + iOS Shortcut 实时补

**为什么**：

| 方案 | 选 / 不选 | 理由 |
|------|--------|------|
| 官方 API | ❌ | 小红书开放平台只对电商商家，没有"我的收藏" |
| MediaCrawler (Playwright) | ✅ **主链路** | 49k stars，最近一周还在更新，复用网页 cookie 不用逆向签名，自带延迟和断点续传 |
| App 抓包 + 逆向签名 (xhshow 类) | ❌ | 算法 2-4 周变一次，工具滞后 1-3 周，封号风险高，性价比极差 |
| 第三方书签 (Cubox/Readwise) | ❌ | 全部不支持小红书 |
| iOS Shortcut → 自建 webhook | ✅ **补充** | 覆盖度只有"短链 + 标题 + 封面"，**但**配合短链反解可以拿到完整内容；**最大优势是延迟近 0** — 你刚收藏完，5 秒就进库 |

**配置规则**（防封号）：
- MediaCrawler 跑在你自己电脑或家里小服务器上，**每周一次**全量同步增量收藏
- 单次抓取 ≤ 200 条，请求间隔 3–5s
- 用网页登录态（扫码登录小红书网页版），**不要**用 App 账号 cookie
- 有住宅代理更好（$5–10/月），没有也能跑

### 决策 2：图片处理 = PaddleOCR + Qwen-VL-Max，**不要**用纯 CLIP

**为什么**：你的查询是"杭州有啥好吃的菌菇"这种**语义 + 具名实体**查询，不是"找视觉相似的图"。
- 纯多模态 embedding（Chinese-CLIP / jina-clip-v2）擅长"以图搜图"，对"店名 / 菜名"这种命名实体几乎无效。
- "图片 → VLM 转结构化文本 → 文本 embedding" 的路线，能让所有信息（店名、菜品、价格）落到统一的可检索文本平面，召回质量高得多。

具体分工：
- **PaddleOCR**（本地，免费）：菜单、价格表、路牌这种**密集文字**图，OCR 准确率 90%+，便宜
- **Qwen-VL-Max**（API，¥0.006/张）：每张图生成一段结构化描述 + 分类标签（菜品/景点/室内/地图截图），中文场景目前性价比最高
- 不用 GPT-4o：贵 5-10 倍，对小红书中文场景没本质优势
- 不本地跑 Qwen2.5-VL-7B：需要 RTX 4090，初始投入 ¥3000+，对个人非隐私场景不划算

### 决策 3：embedding = BGE-M3 本地，**不要**用 OpenAI

**为什么**：
- BGE-M3 在中文短文本（200–500 字）上 SOTA，CPU 都能跑（M 系列芯片更快）
- 自带稀疏向量能力，天然适合 BM25 + 向量混合检索
- 完全本地、零成本、无隐私顾虑
- OpenAI text-embedding-3-large 中文反而不如 BGE-M3

### 决策 4：存储 = 双后端，**自部署 SQLite / 云端 Turbopuffer + Postgres**

**自部署形态**（单租户）：
- SQLite + sqlite-vec + FTS5 一个文件搞定
- 1k–50k 条帖子规模碾压性够用，混合检索一条 SQL，备份 = `cp`

**云端 SaaS 形态**（多租户，**国内市场优先**，目标 1k → 100k 用户）：

| 子系统 | 选型 | 为什么 |
|------|------|------|
| 结构化数据 + 向量 + BM25 一锅端 | **阿里云 PolarDB-PG（含 pgvector）** | 国内访问 <50ms；pgvector 0.7+ 支持 HNSW + 元数据过滤；同一个库做 SQL+向量+全文，少一个组件；按量付费 4c8g 起 ¥1–2/小时，serverless 模式空闲降至 ¥0.3/小时 |
| 大规模向量（>10M 时） | **阿里云 DashVector** | 原生托管向量库，与 Qwen embedding 同 region 零延迟；按 collection 分租户；月成本 1k 用户约 ¥200–400 |
| 对象存储（原图/视频封面） | **阿里云 OSS**，前面套 **CDN（DCDN）** | 国内访问最快；勾选用户 prefix 隔离 + Bucket Policy；冷热分层降成本 |
| LLM / 向量推理 | **阿里云百炼 Qwen-VL-Max + Qwen3 + text-embedding-v3** | 国内 region 同 VPC 直连 0 延迟；与 PolarDB 同账号免费内网带宽；与你的 OCR/抽取/embedding 全部一体 |
| 海外 API（Google Places） | **阿里云香港 region 函数计算** + 跨境内网（CEN） | 见 §12.8 海外出口章节 |
| 用户敏感凭证（小红书 cookie） | **阿里云 KMS**（凭据管家）envelope encryption | 国内合规；与 PolarDB 同 region；个人开发者直接开通 |
| 边缘 / API 网关 | **阿里云 EdgeRoutine** 或 **API 网关 + ALB** | EdgeRoutine ≈ Cloudflare Workers，按请求付费；起步阶段直接 ALB+ECS 也行 |
| 采集 worker（Playwright 长跑） | **阿里云 ECI（弹性容器实例）+ 函数计算 FC 3.0** | 按秒付费；FC 3.0 支持单函数 24h 长任务，比 K8s 便宜 |
| 任务队列 | **PolarDB LISTEN/NOTIFY** 或 **MNS** | 不上 Redis 多一个组件多一份钱 |
| 认证 | **自建 JWT + Casdoor**（开源）或 **阿里云 IDaaS** | Supabase 在国内访问慢；Casdoor 自部署一份在 ECS 即可 |

**为什么国内全栈用阿里云**：
1. **小红书 + 高德 + Qwen 全在国内** — 数据流就在境内闭环，零跨境延迟、零跨境合规风险
2. **PolarDB-PG 一个组件吃下 Postgres + pgvector + 全文搜索（zhparser/pg_jieba）**，初期不用上独立向量库，等用户量到 10k+ 再切 DashVector，**架构线性可扩**
3. **百炼平台同账号**，Qwen-VL-Max 调用走内网，比 Cloudflare/AWS + Qwen 公网调用便宜且快 5–10×
4. **ICP 备案**只能由国内云持证主体协办，阿里云的备案流程对个人开发者最成熟（见 §12.9）

**为什么不是腾讯云 / 火山 / 华为**：
- 腾讯云 VectorDB 也行，但 Postgres 一体化体验不如 PolarDB；适合微信生态深度集成的产品
- 火山引擎更适合做视频/直播/字节系生态产品，对图文 SaaS 没明显优势
- 华为云面向企业，个人开发者体验最差

**为什么不再用 Turbopuffer**：Turbopuffer 在亚太只有日本/新加坡节点，国内用户访问绕一圈，单查询额外 80–150ms，对"目的地工作台秒开"的体验不可接受。**保留作为海外版 Phase 5 备选**。

### 决策 4.5：为什么云端用 Postgres，自部署用 SQLite — 而不是统一

> 这是你重点问的问题，单独成节。

**两个候选的"统一方案"**：

| 方案 | 自部署体验 | 云端可扩展性 | 决策 |
|------|---------|-----------|------|
| 全统一 Postgres | ❌ 用户得装 Docker 跑 Postgres 容器，门槛高 + 占资源 | ✅ 完美 | 否决 |
| 全统一 SQLite | ✅ 一个文件，零依赖 | ❌ 单写者瓶颈，多租户写入会锁；无 RLS；不能托管 | 否决 |
| **libSQL / Turso（SQLite 协议 + 服务端模式）** | ✅ 嵌入式同 SQLite | ⚠️ 多租户能力 2025 才趋于成熟，国内访问慢 | **关注但不主选** |
| **同一套 SQL + 不同 driver**（推荐） | ✅ SQLite 文件 | ✅ Postgres 多租户 | ✅ |

**我们采取第 4 种**：核心 SQL 写到 90%+ 兼容（SELECT/INSERT/UPDATE 都是标准 SQL，向量函数走抽象层），通过 `KnowledgeStore` interface 屏蔽差异。具体：
- 自部署：sqlite-vec 提供 `vec_distance_cosine`，FTS5 提供 `MATCH`
- 云端：pgvector 提供 `<->`，pg_jieba/zhparser 提供 `to_tsquery`
- 应用层只调 `store.search(query, filters)`，看不见底层

**代价**：写两遍数据访问层（约 800 行代码 × 2）。**收益**：
- 自部署用户安装 = `pipx install xhskb` + 一个 db 文件，不用 Docker
- 云端能水平扩展到 100k 用户不换架构
- 备份策略完全不同（SQLite = cp，Postgres = pg_dump 或 PolarDB 自动快照）正好用各自最佳实践

**真正的代码复用**：业务逻辑层（采集、AI 流水线、鲜活度验证、检索融合排序）100% 共享。两边只有"存哪里"不一样。

### 决策 5：鲜活度验证 = **按需触发**，不全量定时

**为什么**：
- 全量定时验证 5000 条收藏，每月几十块 API 费 + 小红书风控 — 没必要
- 真实使用模式：你只有"准备去某城市"时才需要验证，平时不用
- 触发点：用户在前端点开"目的地工作台 → 京都"时，对该城市未在 14 天内验证过的 POI **后台并发验证**，30 秒内全部刷新完

### 决策 6：AI 处理分两档 — **文本默认全开，图片按需深度索引**

VLM 推理是整个系统里最贵的一项（Qwen-VL-Max ¥0.006/张，5000 张就是 ¥30）。把它做成可选档可以把云端版本的免费层成本压到几乎为零。

**两档定义**：

| 档位 | 文本处理 | 图片处理 | 单条帖子成本 | 检索能力 |
|------|--------|--------|--------|--------|
| **L1 标准索引**（默认） | ✅ 摘要 + 结构化抽取 + city 分类 + embedding | ⚠️ **仅 OCR**（PaddleOCR，本地/便宜云）+ 图片缩略图 + 原图 URL | ¥0.005 | 文本全检索；图片按 OCR 文字命中；UI 直接展示原图给用户翻 |
| **L2 深度索引**（可选） | ✅ 同 L1 | ✅ Qwen-VL-Max 每张图生成结构化描述 + 分类标签 + 进 embedding 索引 | ¥0.04 | 跨图语义检索（"找有露台的咖啡馆"能命中虽未提到"露台"两字但图里露台明显的帖子） |

**用户控制粒度**：
- 全局默认（在设置里改）：L1 / L2
- 单条覆盖：进入帖子详情，点"AI 深度索引这条" → 立即升 L2
- 按城市批量升级：在目的地工作台点"为这次京都行升级深度索引" → 该城市所有 POI 帖子升 L2，预计费用提前显示
- 按时间窗口：周末批处理"3 个月内收藏的全部升 L2"

**为什么 OCR 默认要开**：
- PaddleOCR 本地免费，云端走阿里读光也只要 ¥0.001/张
- 小红书图片中"菜单 / 价格表 / 路标 / 攻略图文字版"的占比极高（估计 40%+），不 OCR 等于丢一半信息
- OCR 出来的文字直接进 FTS5/BM25，不用 embedding，零额外成本

**实现要点**：
- Schema 加 `notes.image_index_level INT` (0=未处理, 1=仅OCR, 2=VLM完成)
- 升级时只增量跑 VLM，OCR 结果可复用
- 用户可"降级回 L1" — 只是在向量库里把 image_desc 类型的 chunk 软删，原始结果留着以防再升

### 决策 7：AI 自动城市分类 + 按城市精准刷新

每条帖子在 ingest 时由 LLM 同时打两个核心标签 — **`city`（精确到城市级）** + **`country`** —  作为后续所有"按城市切片"操作的依据。

**为什么必须做**：
- 你的需求里点名了：收藏夹里有 LA / 东京 / 广州，下次去东京只刷东京。所有"按需"操作都依赖准确的 city 字段
- 小红书帖子里的城市信息散落在标题、正文、图片地标、定位标签里，正则 / 关键词匹配 < 60% 准确率，必须 LLM
- 这个分类只跑一次，就跟在结构化抽取一起做（决策 6 的 L1 标准档已包含），无额外成本

**LLM 输出约定**：
```json
{
  "primary_city": "东京",        // 标准化中文名，无歧义
  "primary_country": "JP",        // ISO 3166-1 alpha-2
  "additional_cities": ["京都"],  // 帖子横跨多城市时
  "city_confidence": 0.95,        // <0.7 进人工确认队列
  "trip_purpose": ["food","scenic","shopping"]
}
```

**城市标准化字典**：
- 维护一份 `cities.yaml`，包含别名映射（"洛杉矶 / LA / 罗省 / Los Angeles" → `Los Angeles, US`）
- LLM 输出后做一次本地 fuzzy match 兜底，避免"东京都 / Tokyo / 东京"分裂成 3 个城市

**前端体验（这才是关键）**：

```
首页 / 目的地标签页：

  按收藏数自动排序的城市：
  ┌─────────────────────────────────────┐
  │ 🇯🇵 东京   124 条 · 32 POI · ⚠️ 6 个未验证 │ ← 点这里
  │ 🇺🇸 洛杉矶  89 条 · 21 POI · ✅ 已最新     │
  │ 🇨🇳 广州   67 条 · 18 POI · ✅ 已最新     │
  │ 🇫🇷 巴黎   34 条 · 12 POI                 │
  │ ...                                       │
  │                                            │
  │ [+ 自定义行程] [搜索其他城市]              │
  └─────────────────────────────────────┘

点"东京"：
  → 触发：该城市未在 14 天内验证过的 POI 后台并发刷新
  → 同时：用户已经能看到现有数据 + 进度条
  → 刷新窗口：每个 POI 高德/Google 调一次，按 4 路并发，30 个 POI 约 10 秒完成
  → LA 和广州完全不动，省钱
```

**鲜活度状态机**：
```
fresh    (上次验证 < 7 天)        → 不刷
stale    (7–30 天)                 → 用户开打该城市时后台刷
expired  (>30 天)                  → 直接进刷新队列
new      (从未验证)                → 同上
```

---

## 4. 数据 Schema（Phase 1 落地版）

```sql
-- 帖子原始信息
-- 云端版所有表都带 tenant_id，并启用 RLS；自部署版可忽略。
CREATE TABLE notes (
  id            TEXT PRIMARY KEY,        -- 小红书 note_id
  tenant_id     TEXT,                    -- 仅云端用，RLS 过滤键
  url           TEXT,
  title         TEXT,
  body          TEXT,
  author        TEXT,
  published_at  TIMESTAMP,
  saved_at      TIMESTAMP,               -- 你收藏的时间
  image_paths   TEXT,                    -- JSON array, 本地路径或 R2 key
  primary_city  TEXT,                    -- 决策 7：LLM 打的城市标签，所有“按城市切片”操作都靠这个
  primary_country TEXT,                  -- ISO 3166-1 alpha-2, 如 'JP'
  city_confidence REAL,                  -- <0.7 进人工确认队列
  trip_purpose  TEXT,                    -- JSON: ["food","scenic",...]
  text_index_status TEXT,                -- pending / done / failed
  image_index_level INT DEFAULT 1,       -- 决策 6：0=未处理, 1=仅OCR(默认), 2=VLM 深度
  ai_summary    TEXT                     -- 一句话摘要 (LLM 生成)
);

-- POI 主表 (一条帖子可能命中多个 POI)
CREATE TABLE pois (
  id            INTEGER PRIMARY KEY,
  tenant_id     TEXT,                    -- 仅云端
  canonical_name TEXT,                   -- "记记馄饨坊"
  aliases       TEXT,                    -- JSON: ["藏在巷子里的馄饨", ...]
  city          TEXT,                    -- "杭州"
  country       TEXT DEFAULT 'CN',
  poi_type      TEXT,                    -- restaurant / cafe / scenic / shop / hotel
  lat REAL, lng REAL,
  amap_id       TEXT,                    -- 高德 POI id
  google_place_id TEXT,
  address       TEXT,
  UNIQUE(tenant_id, canonical_name, city)
);

-- 帖子 ↔ POI 关联，带这条帖子里对该 POI 的具体推荐
CREATE TABLE note_poi (
  note_id       TEXT,
  poi_id        INTEGER,
  must_try      TEXT,                    -- "松茸炖鸡"
  price_per_person INTEGER,
  highlight     TEXT,                    -- 用户当时为啥收藏的 LLM 概括
  sentiment     REAL,                    -- 帖子作者情感 0-1
  PRIMARY KEY (note_id, poi_id)
);

-- 鲜活度
CREATE TABLE poi_freshness (
  poi_id        INTEGER PRIMARY KEY,
  status        TEXT,                    -- alive / suspect / closed / unknown
  score         INTEGER,                 -- 0-100
  signals       TEXT,                    -- JSON: { amap_rating, recent_xhs_count, neg_keyword_ratio, ... }
  last_checked_at TIMESTAMP
);

-- 向量 (sqlite-vec)
CREATE VIRTUAL TABLE note_chunks_vec USING vec0(
  chunk_id   INTEGER PRIMARY KEY,
  embedding  FLOAT[1024]
);

-- BM25 全文 (FTS5)
CREATE VIRTUAL TABLE note_chunks_fts USING fts5(
  chunk_id, text, tokenize='porter unicode61'
);

CREATE TABLE note_chunks (
  id        INTEGER PRIMARY KEY,
  note_id   TEXT,
  chunk_type TEXT,                       -- title / body / image_desc / ocr / poi_card
  text      TEXT,
  image_idx INTEGER                      -- 哪张图，便于前端高亮
);

-- Inbox 工作流（Phase 1.5 落地）：扫描收藏夹拿到的候选笔记在正式
-- 入库前先进这个 inbox，人工筛选 + 多标签聚类后再 promote 到 notes。
CREATE TABLE note_inbox (
  note_id      TEXT PRIMARY KEY,
  url          TEXT, title TEXT, cover_url TEXT, author TEXT,
  status       TEXT,                    -- pending / discarded / promoted
  cluster_id   TEXT,                    -- is_primary=1 那行的镜像（向后兼容）
  raw_meta     TEXT,                    -- JSON：xsec_token / xsec_source / page_url
  discovered_at TEXT, saved_at TEXT, promoted_at TEXT
);
CREATE TABLE note_cluster_tags (        -- 多标签：一条笔记最多 2 个簇标签
  note_id    TEXT, cluster_id TEXT,
  score      REAL, is_primary INTEGER,
  source     TEXT, created_at TEXT,
  PRIMARY KEY (note_id, cluster_id)
);
CREATE TABLE inbox_clusters (id, name, description, count, ...);
```

> Inbox / 多标签聚类的完整阈值与流程见 [docs/clustering.md](./docs/clustering.md)、爬虫使用见 [docs/crawler.md](./docs/crawler.md)。

---

## 5. AI 处理流水线（具体 prompt 思路）

### 5.1 单图处理 prompt（Qwen-VL-Max）

```
你是小红书内容索引助手。请分析这张图片，输出严格 JSON：

{
  "scene": "dish | scenic | indoor | menu | map | product | portrait | other",
  "description": "一句话客观描述（中文，30-80字）",
  "extracted_text_relevant": "图中对检索有意义的文字（菜名/店名/价格/地名），无则空",
  "tags": ["标签1", "标签2", ...]   // 例: ["日式","刺身","深夜食堂"]
}

不要描述无关细节，不要主观评价。
```

### 5.2 整帖结构化抽取 prompt

```
基于以下小红书帖子（含正文、图片描述、OCR 结果），抽取一个或多个 POI（兴趣点）：

{
  "summary": "20 字内一句话总结这条帖子",
  "city": "城市，明确写出，无则 null",
  "trip_purpose": ["food","scenic","cafe","shopping","photo","stay"],
  "pois": [
    {
      "name": "店名/景点名（如帖子未明说则给一个识别性描述）",
      "type": "restaurant|cafe|scenic|shop|hotel|other",
      "must_try": "核心推荐（一道菜/一个机位/一件商品），无则 null",
      "price_per_person": 数字或 null,
      "address_hint": "区/商圈/街道，无则 null",
      "highlight": "作者为啥推荐（≤30字）"
    }
  ]
}
```

**可靠性兜底**：JSON Schema 校验失败 → 自动降级到 GPT-4o 重试一次（每月几条不会贵）。

### 5.3 chunk 切分策略

每条帖子产出多种 chunk，全部进 `note_chunks_vec` + `note_chunks_fts`：
- `title` chunk
- `body` chunk（>500 字时按段切）
- 每张图一个 `image_desc` chunk（VLM 描述 + tags 拼接）
- 每张含文字图一个 `ocr` chunk
- 每个 POI 一个 `poi_card` chunk（"记记馄饨坊 · 杭州 · 馄饨 · 必点松茸馄饨 · 人均 35"）

`poi_card` 是检索召回率最高的 chunk，是查询命中的主力。

### 5.4 当前实现：从收藏到入库的完整链路

设计上的「ingest 流水线」对应代码里的两个文件：

- [`backend/app/services/inbox_promote.py`](backend/app/services/inbox_promote.py) — `promote_notes()` 是入口。从 inbox 取出选定的 `note_id`，对每条：
  1. 状态置 `processing`；
  2. 调 `XhsDetailCrawler` 或 `fetch_note_detail_http`（cookie-less SSR 路线，见 [docs/crawler.md](docs/crawler.md)）抓正文 + 图片 + 标签；
  3. 喂给 `ingest_note`；
  4. 状态置 `promoted`，失败置 `failed` 并把短错误存进 `last_error`。

- [`backend/app/services/ingest.py`](backend/app/services/ingest.py) — `ingest_note(store, raw)` 把单条原始数据落库：
  1. LLM 抽取 `primary_city` / `primary_country` / `purposes` / `summary` / `pois`（兜底：扫文本里的城市别名）；
  2. 构建 chunks：`title` + `body`（按段切）+ 每个 POI 一个 `poi_card`（**当前还没接图片 OCR / VLM 描述 chunk**，§5.1/5.3 里的 `image_desc` 和 `ocr` 仍是 TODO）；
  3. 用 e5 embed 所有 chunks；
  4. `store.upsert_note(...)` 一次写入 `notes` + `note_chunks` + `pois` + `note_poi`。

整批 promote **同步执行**（无后台队列），用 `max_per_request` 上限挡 HTTP 超时，UI 端切片提交。

---


## 6. 鲜活度验证策略

### 6.1 多信号融合评分（0–100）

| 信号 | 权重 | 数据源 |
|------|------|--------|
| 营业状态字段 | 40% | Google `business_status` / 高德返回是否仍存在 |
| 最近 60 天小红书新帖数量 | 25% | 小红书搜索 |
| 评分 vs 历史均值 | 15% | 高德 / Google rating |
| 最近评论负面关键词比例 | 15% | Google reviews / 大众点评爬取 |
| 帖子年龄 vs 品类半衰期 | 5% | 本地计算（咖啡 90 天 / 餐厅 150 天 / 景点 365 天） |

输出：
- `alive` (≥75)：绿色徽章 ✅
- `suspect` (40–74)：黄色徽章 ⚠️ "评分下降 / 帖子较旧，建议查证"
- `closed` (<40 或 `business_status=CLOSED_PERMANENTLY`)：红色徽章 ❌

### 6.2 触发时机

```
1. 用户打开"目的地工作台 → 城市X" 
   → 后台并发验证该城市 14 天内未验过的 POI
2. 用户对某 POI 点"加入行程" 
   → 强制实时验证一次
3. 不主动定时刷
```

### 6.3 成本预算

| 操作 | 单价 | 触发频次 |
|------|------|---------|
| 高德搜索+详情 | ¥0.006/POI | 你出行前才查 |
| Google Places Search+Details (Pay-as-you-go) | $0.05/POI | 海外才用 |
| 1 次出行（覆盖 50 个 POI 验证） | ≈ ¥1 国内 / $2.5 海外 | 完全可接受 |

---

## 7. UX 设计（**这是这个项目的灵魂**）

### 7.1 主界面 ≠ 搜索框

太多类似项目失败在做了个 ChatGPT 包皮。真实使用习惯下，搜索框是**第三入口**。前两个是：

#### 入口 ①：目的地工作台（首页主卡片）

```
┌─────────────────────────────────────────────────┐
│  🗾 京都 · 6 天 5 晚 (你计划中)                  │
│  ─────────────────────────────────────────────  │
│  你收藏过 47 条相关 · 12 个 POI · 8 已验证 ✅   │
│                                                  │
│  🍱 美食 (18)        ☕ 咖啡 (9)                │
│  ⛩  景点 (11)        🛍  购物 (6)               │
│  📷 拍照机位 (7)     🏨 住宿 (3)                │
│                                                  │
│  [打开行程板] [按地区分组] [导出地图]             │
└─────────────────────────────────────────────────┘
```

点开任一分类 → 卡片瀑布流，每张卡片：
- POI 名 + 鲜活度徽章
- 你当初为啥收藏（LLM 一句话总结）
- 缩略图 3 张
- "详情 / 加入行程 / 已去过 / 不感兴趣"四个动作

**核心交互**：用户**不需要再去翻原帖**，AI 摘要+原图缩略足够决策。要原帖一键跳转。

#### 入口 ②：被动推送（复用率第一）

- "你 2024 年 8 月收藏的『大阪心斋桥拉面店』评分跌到 3.8，新帖里有人说换老板了，要从你的清单移除吗？"
- "周日早 10 点：你最近收藏了 6 家上海安福路的咖啡店，要不要这个周末安排一下？"
- "出发前 3 天：京都行程里 3 个 POI 还没验证，现在帮你查一下？"

实现方式：本地 cron + 系统通知 / iOS 推送。

#### 入口 ③：自然语言搜索

留在二级入口，处理长尾查询：
- "找我收藏过的、人均 200 以内、有露台的咖啡店"
- "我之前是不是收藏过教堂婚礼场地"

走 SQL（city/price/type 过滤）+ 向量召回 + LLM rerank，三段式。

### 7.2 单帖详情页

- 顶部：原帖完整内容 + 图片轮播
- 中部："AI 看这条帖子"折叠面板：摘要、命中的 POI、抽取出的菜品/价格
- 底部：相关收藏（"你还收藏过这家店的其他帖子" / "同城同类型其他选择"）

### 7.3 设置页（一次性配好就忘）

- 小红书登录态（扫码续期，每 60 天提醒一次）
- API Key（Qwen / Google）
- 同步频率（默认每周日 02:00）
- 隐私模式（关掉云 API 后退化为本地小模型路线，速度更慢但完全离线）

### 7.4 认证你的小红书账号 — 怎么做

你说"鉴权没问题"。最干净的做法：
1. 桌面端启动应用，弹一个 Playwright 控制的 Chrome 窗口
2. 你扫码登录小红书网页版
3. 应用把 cookie（`a1`/`web_session`/`webId`）加密存到本地 keychain
4. 后续 MediaCrawler 复用这份 cookie 自动跑
5. cookie 过期前 7 天通知你重新扫码

**不要**：让你输密码、把账号给云服务、上传 cookie 到任何远端。

---

## 8. 法律与风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| 违反小红书 ToS | 🟡 中 | 仅个人使用、不二次分发、不商业化；低频抓取（每周一次） |
| 账号封禁 | 🟡 中 | 用网页登录态 + 3-5s 间隔 + 单次≤200 条；准备小号兜底；MediaCrawler 实测可稳定数月 |
| 数据隐私 | 🟢 低 | 全本地存储，可选 SQLCipher 加密；API 调用时只传内容不传账号信息 |
| 著作权 | 🟢 低 | 个人离线使用，不构成"公开传播" |
| API 成本失控 | 🟢 低 | 月度 ¥10 以内，且按需验证天然防爆 |

**红线**：不分享、不导出给他人、不公开发布索引内容。这条守住，风险可控。

---

## 9. 成本预算（实测口径）

### 9.1 自部署用户（单机，己收藏 1000 条）

| 项目 | L1 标准（默认） | L2 深度（可选） |
|------|--------------|--------------|
| PaddleOCR 本地处理 5000 张图 | 0 | 0 |
| Qwen JSON 抽取 + 城市分类 1000 条 | ¥3 | ¥3 |
| Qwen-VL-Max 图片描述 5000 张 | — | ¥30 |
| BGE-M3 embedding (本地) | 0 | 0 |
| 高德 POI 首次标注 800 个 | ¥5 | ¥5 |
| **一次性合计** | **≈ ¥8** | **≈ ¥38** |
| **月度增量（200 条/月 + 1 次出行验证）** | **≈ ¥2** | **≈ ¥10** |

### 9.2 云端 SaaS（你作为运营者）

**单用户边际成本（假设平均 3000 条收藏，150 条/月增量，20% 用户开 L2）**：

| 项目 | L1 用户 | L2 用户 |
|------|---------|---------|
| LLM API（抽取 + OCR 云版） | ¥1.2/月 | ¥1.2/月 |
| Qwen-VL-Max 图片 | — | ¥5/月 |
| Turbopuffer 存储 + 查询 | ¥0.5/月 | ¥1.0/月 |
| Postgres / R2 / 带宽 / 高德验证 | ¥1/月 | ¥1/月 |
| **边际成本** | **¥3/月** | **¥8/月** |

**定价策略建议**：
- 免费档：仅 L1 + 500 条以内收藏 + 手机 Web（拉新用户）
- Pro：¥19/月，L1 不限量 + 50 张图/月 L2 锐锅 + 原生 iOS Share Extension + 跨设备同步
- Pro Plus：¥39/月，L2 不限量 + 优先验证队列 + 行程导出为 Notion/Apple地图
- 1000 Pro 用户：毛收入 ¥19k/月，边际成本 ¥3–6k，淘汰后毛利 ≈ ¥13k/月。能越过个人开发者協同服务本。

### 9.3 关键成本起雷防御

- **用户调起 100 万条收藏要求 L2**：进入手动上闸，系统需预算纯预估总费 + 用户确认才跑
- **某用户被小红书反爬**：以用户为颗粒度隔离，单人出事不传染
- **恶意刷 LLM**：每用户月度 LLM 调用有硬上限（免费 ¥2 / Pro ¥10 / Pro+ ¥25），满额后提示控件购买 top-up

---

## 10. 落地路线图

> 按"先开源自部署 → 抽接口 → 上云 SaaS"的产品节奏，对应你"想推广出去给大家用"的目标。

### Phase 1 · 自部署 MVP（1–2 周，CLI）

- [x] MediaCrawler 跑通"我的收藏"导出（自研 Playwright/CDP 实现，见 [docs/crawler.md](docs/crawler.md)）
- [x] **`KnowledgeStore` 接口**（这一步决定后面能不能一套代码两形态）：定义 ingest / search / mark_freshness 等抽象方法
- [x] SQLite 实现该接口
- [x] 接 LLM JSON 抽取（含 city 自动分类）— 当前用 Azure OpenAI / Qwen / Mock 三档可切换；PaddleOCR 暂未接入
- [x] 本地 embedding：multilingual-e5-base on MPS（替代 BGE-M3，见 [docs/embedding.md](docs/embedding.md)）
- [x] sqlite-vec + FTS5 混合检索
- [ ] CLI: `xhskb search "京都拉面"` / `xhskb refresh-city 东京`（搜索能力已在 backend 内实现，但还没包成 CLI 命令）

**交付物**：开源仓库，能在 Mac/Linux 上 `pipx install` 后直接用。

### Phase 2 · 自部署 Web UI + 手机访问（2 周）

- [x] FastAPI 后端 + Next.js 前端（PWA 化待办）
- [x] 目的地工作台 + 城市卡片首页
- [x] 单帖详情 + 图片画廊
- [x] 我的收藏 inbox + 多标签自动聚类（见 [docs/clustering.md](docs/clustering.md)）
- [ ] 高德 POI 解析 + 鲜活度按需验证（按城市批量）
- [ ] 增量同步定时任务（cron）— 目前是 UI 上点「扫描收藏夹」手动触发
- [ ] **Tailscale Funnel 一键集成**：用户跑 `xhskb expose` 自动起隧道，手机扫二维码加书签
- [ ] L1/L2 分级处理 UI

**交付物**：自部署用户在自己 Mac 上 `docker compose up` 就能用，手机能访问。

### Phase 3 · 云端 SaaS（4–6 周）

- [ ] **第二个 `KnowledgeStore` 实现**：Turbopuffer + Postgres
- [ ] Supabase Auth + RLS schema（所有表加 `tenant_id`）
- [ ] R2 对象存储 + 用户 prefix 隔离
- [ ] AWS KMS envelope 加密用户的小红书 cookie
- [ ] 计费集成（Stripe / 国内走 Lemonsqueezy 或微信支付）
- [ ] 后台采集 worker 池（每用户一个限速桶 + 共享代理池）
- [ ] L1 默认 / L2 按需 + 月度配额硬上限
- [ ] 异地多活？**先不上**，单 region（亚太或欧洲）够 1k 用户

**交付物**：xhs-kb.com（或类似域名）公测，自部署版本继续维护。

### Phase 4 · 原生 iOS App（仅 SaaS 用户必需，2–3 周）

- [ ] SwiftUI 客户端 + Share Extension（**核心增值**：在小红书点分享 → 直接进库）
- [ ] App Store 上架（这一步是和自部署用户的核心差异点）
- [ ] 推送通知（出行前提醒、塌房通知）

### Phase 5 · 长期演进

- 完全本地模式：Mac MLX 跑 Qwen2.5-VL-7B（隐私党的福音）
- 多人共享某个目的地清单（伴侣同游、朋友推荐）
- 微信 / Telegram bot 做对话式查询
- 行程一键导出：Apple 地图 / Google Maps 收藏 / Notion 数据库

---

## 11. 现在就可以做的下一步

1. **确认你的收藏数量级**：去小红书看一下"我的收藏"大概多少条？决定 Phase 1 的处理时长
2. **决定双形态优先级**：是想先做能自己跑起来的开源版（Phase 1）就好，还是已经笃定要做 SaaS（Phase 3）— 影响第一周要不要花时间在 `KnowledgeStore` 接口抽象上
3. **申请 Qwen API Key**（[阿里 Dashscope](https://dashscope.aliyuncs.com)），充 ¥50 够回溯整库 + 跑一年
4. **如果出国玩多**：开 Google Maps Platform，按用量付费
5. 我可以直接帮你把 Phase 1 脚手架写出来 — 项目目录、依赖、跑通 1 条样本的 demo。要的话告诉我。

---

## 12. 云端 SaaS 架构详解（**国内市场优先**）

> 设计前提：用户 90%+ 在中国大陆，需要国内访问 <200ms，海外 API（Google Places）通过香港跨境出口。全栈选阿里云，理由见 §3 决策 4。

### 12.1 物理拓扑

```
                    ┌────────────────────────────┐
                    │       国内用户设备           │
                    │  iOS App / PWA / 微信小程序  │
                    └────────┬───────────────────┘
                             │ HTTPS + JWT (国内 CDN: DCDN)
                             ▼
                    ┌────────────────────────────┐
                    │  阿里云 杭州/上海 region    │
                    │  ALB + EdgeRoutine          │
                    │  - 路由 + 鉴权               │
                    │  - 速率限流（per tenant）    │
                    └────────┬───────────────────┘
                             │
        ┌────────────────────┼────────────────────────────┐
        ▼                    ▼                            ▼
 ┌──────────────┐    ┌──────────────────┐      ┌──────────────────┐
 │  PolarDB-PG  │    │  阿里云 OSS       │      │ 阿里云百炼        │
 │  + pgvector  │    │  (原图 / 视频封面) │      │ Qwen-VL-Max       │
 │  + zhparser  │    │  prefix=tenant/<id│      │ Qwen3 / embedding │
 │  + RLS       │    │  + DCDN 加速      │      │ (同 region 内网)   │
 └──────┬───────┘    └──────────────────┘      └──────────────────┘
        ▲
        │ LISTEN/NOTIFY
        ▼
 ┌──────────────────────────────────────────────────┐
 │   阿里云 函数计算 FC 3.0 + ECI                     │
 │   - 采集器（每用户一个 Playwright 实例 + 住宅代理）  │
 │   - AI 处理 worker（直连百炼内网）                  │
 │   - 国内鲜活度验证（高德 POI 同 region）            │
 │   - 加密 cookie 解封 → 用 → 销毁                    │
 └──────┬───────────────────────────────────────────┘
        │
        │ envelope-encrypted
        ▼
 ┌──────────────────┐         跨境通道（CEN/GA）
 │  阿里云 KMS      │     ┌──────────────────────────┐
 │  凭据管家         │     │  阿里云香港 region 函数计算 │
 └──────────────────┘     │  - Google Places API 调用  │
                          │  - OpenAI/Gemini 兜底       │
                          └──────────┬───────────────┘
                                     │
                                     ▼ 公网出海
                              ┌──────────────┐
                              │ Google APIs  │
                              │ OpenAI APIs  │
                              └──────────────┘
```

### 12.2 多租户隔离三层防御

```
┌─────────────────────────────────────────────────────────────┐
│ 第 1 层：JWT 中签发 tenant_id，所有请求强制带                │
│ 第 2 层：PolarDB RLS — 即使 SQL 拼错也读不到他人数据         │
│         CREATE POLICY notes_tenant ON notes                  │
│           USING (tenant_id = current_setting('app.uid'));   │
│ 第 3 层：OSS prefix = `tenant/<tenant_id>/...`               │
│         RAM Policy 限制；DashVector collection per tenant   │
└─────────────────────────────────────────────────────────────┘

任意一层失效，其他两层兜底。一个人维护的项目最怕跨户泄露事故，
三层冗余是性价比最高的保险。
```

### 12.3 用户敏感凭证（小红书 cookie）的存储

```
注册 / 绑定时:
  用户扫码 → 浏览器拿到 cookie
    ↓ HTTPS POST (DCDN → ALB)
  FC 函数拿到明文 cookie
    ↓
  调用阿里云 KMS GenerateDataKey → (DEK_plain, DEK_encrypted)
    ↓
  AES-256-GCM(plaintext=cookie, key=DEK_plain) → ciphertext
    ↓
  立即把 DEK_plain 从内存销毁（zeroize）
    ↓
  存 PolarDB: { tenant_id, ciphertext, DEK_encrypted, iv, tag }

使用时（采集 worker 跑前）:
  从 DB 读 (ciphertext, DEK_encrypted)
    ↓
  调用 KMS Decrypt(DEK_encrypted) → DEK_plain
    ↓
  AES-256-GCM 解密 → cookie 明文
    ↓
  注入 Playwright，用完后立刻 zeroize
    ↓
  cookie 明文永不落盘、永不进 log
```

阿里云 KMS 操作审计日志全部开，cookie 异常解封触发告警。

### 12.4 后台 Worker 调度

每用户一个**独立的逻辑 worker**（不是物理实例，是 FC 池里按 tenant_id 串行的任务流），原因：
- 小红书风控按账号粒度封，把不同用户的请求混在一个 IP 池里跑会让全平台用户连坐
- 每用户绑一个**住宅代理 IP**（国内代理服务，成本 ~¥3/用户/月，做进 Pro 价里）
- 同一用户的请求**严格串行 + 延迟 3–5s**，不并发

LLM 调用走共享池但带 tenant_id 限速 + 月度配额硬墙，防止单用户烧光预算。

### 12.5 采集策略（云端关键差异）

自部署版本：用户自己电脑做 Playwright，IP 是用户家宽带，**完美**。

云端版本：
- 用户电脑不在线时也要采集 → 必须在云端跑 Playwright
- 但云 IP 段 + 全部用户的 cookie 一起跑 = 风控天堂
- **解法**：每用户绑一个**住宅代理出口**（国内代理：青果、巨量、芝麻；按 tenant_id 路由到对应 IP），让云上的请求看起来像从用户家发的
- 代理服务可选：[Bright Data](https://brightdata.com/) / [Oxylabs](https://oxylabs.io/) / 国内的青果代理
- 同时：**默认每周一次**全量同步，新帖通过 iOS Share Extension 实时入库（不依赖采集）

如果代理成本扛不住，**降级方案**：让用户在自己电脑上跑一个轻量"采集 agent"（Electron 或 Tauri 小工具），云端只负责存储和检索，agent 把数据推到云。这样云端零代理成本，但用户多一步装 app 的门槛。Pro 用户里大约 30% 愿意装。

### 12.6 GDPR / 数据删除

```
DELETE /api/account
  ↓
1. 立即标记 users.is_deleted=true，所有读取接口立刻失效
2. 30 天软删期（用户可恢复）
3. T+30: cron job
   - DELETE FROM PolarDB WHERE tenant_id = X (CASCADE)
   - DashVector DELETE collection user_X（如已迁移）
   - OSS batch delete prefix tenant/X/
   - KMS schedule key deletion (该用户的 DEK)
   - 写入审计日志（保留 3 年合规，按《个保法》要求）
GET /api/account/export → 异步生成 zip，含全部 raw + 索引 JSON，邮件链接 7 天有效
```

### 12.7 可观测性最小集

- 阿里云 ARMS（应用监控）：异常报警 + 链路追踪，比 Sentry 国内访问快
- 阿里云 SLS（日志服务）：所有 FC 日志自动入库，查询用 SQL
- PolarDB 自带慢查询 + 性能洞察
- 单人维护，不自建 Grafana/Prometheus

### 12.8 海外 API 出口（**国内云访问 Google / OpenAI 的关键**）

这是国内做 SaaS 最容易卡的环节。**主推方案：阿里云 CEN（云企业网）+ 香港 region 中转**。

#### 主推方案：杭州 region ↔ 香港 region 内网打通

```
国内用户 → 阿里云杭州 FC（主 worker）
              │
              │ 当需要调 Google Places：
              ▼
         阿里云 CEN 跨境带宽（按 Mbps 包月，5Mbps 起约 ¥800/月）
              │
              ▼
         阿里云香港 region FC （海外出口）
              │
              ▼ 公网（香港 → Google 直连，~30ms）
         Google Places API / OpenAI API
```

**优点**：
- 跨境通道是阿里云官方合规产品，**无政策风险**（不属于"翻墙"，是企业跨境互联网服务）
- 国内 → 香港延迟 30–50ms，香港 → Google 30ms，单次 API 总延迟 100–150ms 可接受
- 香港 region 用国际 BGP，调 Google/OpenAI 不需要任何代理

**成本估算**：
| 项目 | 配置 | 月费 |
|------|------|------|
| CEN 跨境带宽 | 5 Mbps（≈ 1.6TB/月理论上限，实际跑不满） | ¥800 |
| 香港 FC 函数 | 每次 100ms × 月 5 万次海外调用 | ¥10–30 |
| 香港 region OSS（不建议存大数据） | 仅存少量缓存 | ¥10 |
| **合计** | | **¥820–840/月** |

**MVP 阶段省钱方案**（月调用 < 1 万次）：用 **按量计费的全球加速 GA**，跨境流量 ¥0.8/GB，月调用 1 万次 × 5KB = 50MB ≈ ¥40。等用户起来再升级到包月 CEN。

#### 备选方案对比

| 方案 | 成本 | 延迟 | 合规 | 推荐度 |
|------|------|------|------|--------|
| **CEN + 香港 region**（主推） | ¥800–1500/月 | 100–150ms | ✅ 官方 | ⭐⭐⭐⭐⭐ |
| 阿里云 GA（按量） | ¥40–200/月（看流量） | 120–200ms | ✅ 官方 | ⭐⭐⭐⭐（MVP） |
| 直接放在阿里云香港 region | 无跨境费 | 国内用户 → 香港 ~50ms | ✅ 官方 | ⭐⭐⭐（牺牲国内体验） |
| 第三方 OpenAI 中转（如 API2D） | 按量，便宜 | 不稳 | ⚠️ 灰色 | ⭐⭐（不推荐生产） |
| 自建 V2Ray/clash | 极便宜 | 不稳 | ❌ 违规 | ❌ 绝对不要 |

#### 替换策略：能不出国就不出国

实际上 90% 的用户主要在国内旅游 + 东南亚/日韩，海外需求集中在小部分用户：
- **国内**：高德 POI（覆盖完整）+ 小红书反查 — **零跨境**
- **日韩 / 东南亚**：高德海外 POI 在主要城市覆盖度其实尚可（东京、首尔、曼谷、新加坡的餐厅 80%+ 能命中），**先尝试高德兜底**
- **欧美**：必须 Google Places — 走香港出口
- **小语种偏远地区**：免谈，标"未验证"留给用户判断

策略：在代码里做一个 `freshness_provider_chain`：高德 → 高德海外 → Google Places（仅最后一步走跨境）。**用户出海需求的 70%+ 流量打不到 Google**，跨境带宽真实月用量很小。

### 12.9 ICP 备案 + 算法备案策略

**两个备案的区别**：
- **ICP 备案**：网站 / 域名备案，所有国内云 region 服务对外开放都需要。个人主体可办。
- **算法备案**（《互联网信息服务深度合成管理规定》《生成式人工智能服务管理暂行办法》）：用了生成式 AI 给"公众"提供"内容生成 / 推荐"服务的需要做。**这是 2024 年起逐步收紧的红线**。

**这个产品要不要算法备案**？
- 如果定位是"个人收藏管理工具"（用户索引自己的私有数据，AI 输出仅给本人看，不向公众生成内容） — **大概率不需要算法备案**
- 如果做"为你推荐别人收藏的 XX 攻略"（向公众做内容推荐） — **需要**

**对策**：
1. 产品对外宣传严格定位"私人知识库 / 收藏整理工具"，不出现"AI 生成内容""智能推荐"这类敏感词
2. 用户 A 的索引结果绝不暴露给用户 B（这本来就是隐私要求）
3. 不做内容创作功能（"帮我生成一篇京都攻略" — 这种就要算法备案了）
4. 万一未来要做内容生成，提前找 [国家网信办深度合成服务算法备案系统](https://beian.cac.gov.cn/) 走流程，3–6 个月

**ICP 备案流程（个人）**：
- 阿里云 / 腾讯云都有备案小程序，全程线上 + 短视频审核
- 个人主体限制：**域名不能含"公司""集团"等字样**，**网站名不能含"商城""平台""科技"** 等敏感词
- 产品名建议起个文艺名字（如 `xhskb.app` 在国内备案叫"小本本""收藏簿"之类）
- 周期：阿里云 7–20 天

**先不备案的过渡方案**：
- 域名解析到 **阿里云香港 region**，对国内用户开放（不需要 ICP）
- 牺牲：国内访问延迟从 50ms 涨到 80–120ms（可接受）
- 优点：MVP 期间不被备案流程卡住，先验证产品再回来补
- **正式商业化前必须补备案**，否则被举报会一夜下线

### 12.10 国内云月度成本估算

#### MVP 期（≤ 100 用户）

| 项目 | 配置 | 月费 |
|------|------|------|
| PolarDB-PG Serverless | 1–2 PCU，空闲降至 1 PCU | ¥150 |
| OSS 存储 + DCDN | 100GB + 200GB 流量 | ¥80 |
| FC 函数计算 | 月 100 万次调用 | ¥30 |
| ECI 弹性容器（Playwright） | 每天累计 5 小时 | ¥50 |
| 阿里云 KMS | 5000 次操作 | ¥10 |
| Qwen API | 100 用户 × ¥1/月 | ¥100 |
| 高德 POI | 月 5 万次 | ¥30 |
| 跨境（GA 按量） | 海外 API 月 1 万次 | ¥40 |
| 域名 + ICP 备案 | 一次性 ¥55/年 | ¥5 |
| 监控（ARMS + SLS） | 基础包 | ¥30 |
| **合计** | | **≈ ¥525/月** |

**用户付费平衡点**：30 个 Pro 用户（¥19/月）≈ ¥570，**MVP 阶段就能持平**。

#### 成长期（1000 用户，30% Pro 转化）

| 项目 | 配置 | 月费 |
|------|------|------|
| PolarDB-PG | 4c8g + 1 个只读副本 | ¥1500 |
| OSS + DCDN | 1TB 存储 + 2TB 流量 | ¥600 |
| FC + ECI | 月 5000 万次 / Playwright 每天 50h | ¥800 |
| Qwen API | 1000 用户 × 平均 ¥3 | ¥3000 |
| 高德 POI（包月企业版） | | ¥300 |
| 跨境（CEN 5Mbps 包月） | | ¥800 |
| 住宅代理（300 个 Pro 用户 × ¥3） | | ¥900 |
| KMS / 监控 / 杂项 | | ¥200 |
| **合计** | | **≈ ¥8100/月** |

**收入**：300 Pro × ¥19 + 50 Pro+ × ¥39 ≈ **¥7700/月** — 接近持平。
**真正赚钱**：5000 用户、30% 付费转化（约 1500 付费）后毛利能上 ¥15k/月。

#### 自部署用户成本（用户视角，月度）

| 项目 | L1 标准 | L2 深度 |
|------|--------|--------|
| Qwen API（用户自付） | ¥2 | ¥10 |
| 电脑常开（按平均功耗摊） | ¥10 | ¥10 |
| 域名（可选，用 Tailscale 不需要） | ¥0 | ¥0 |
| **合计** | **¥12** | **¥20** |

**结论**：自部署用户对你（运营者）**零成本**，对用户每月十几块；云端 SaaS 单用户边际成本 ¥3–8（已含跨境带宽分摊），定价 ¥19 健康。

---

## 13. 自部署形态详解

### 13.1 用户安装路径

```
推荐路线 A：Docker Compose（80% 用户）
  $ git clone github.com/you/xhs-kb
  $ cp .env.example .env  # 填 Qwen API key
  $ docker compose up -d
  → 浏览器打开 localhost:3000，扫码登录小红书 → 完成

推荐路线 B：原生 Mac App（M 系列推荐）
  $ brew install xhs-kb
  → 后台跑，菜单栏图标，PaddleOCR 用 CoreML 加速

不推荐：
  - K8s（小白劝退）
  - 独立 Python venv 安装（依赖问题多）
```

### 13.2 自部署用户的手机访问

**推荐：Tailscale Funnel**（免费、零配置、HTTPS 自动）

```
$ xhs-kb expose       # 包内集成 tailscale serve

→ 输出二维码 + URL: https://your-machine.your-tailnet.ts.net
→ 手机扫码加书签 → 装 PWA → 完事
→ 任何网络都能访问，自动 HTTPS
```

**为什么不用 ngrok**：免费版 URL 每次变，付费版贵且不稳。

**为什么不用 Cloudflare Tunnel**：配置门槛高，对不会改 DNS 的用户不友好。

**iOS 限制要写清楚**：自部署用户的 PWA**无法**接小红书的"分享菜单"（这是 iOS Share Extension，需上架 App）。补救方案：教用户用 iOS Shortcut，长按小红书帖子 → "拷贝链接" → 触发 Shortcut → POST 到自己 Tailscale URL。**功能等价但多一步**。

### 13.3 云端 vs 自部署关键功能对照

| 功能 | 云端 SaaS | 自部署 |
|------|---------|------|
| 自然语言搜索 | ✅ | ✅ |
| 目的地工作台 | ✅ | ✅ |
| 鲜活度按需验证 | ✅ | ✅ |
| 城市自动分类 | ✅ | ✅ |
| L1/L2 分级 | ✅ | ✅ |
| 周期性自动采集 | ✅ 云上跑 | ⚠️ 电脑得开机 |
| 手机随时打开 | ✅ 秒开 | ⚠️ 需启 Tailscale |
| iOS 原生分享入库 | ✅ Share Extension | ❌（用 Shortcut 兜底） |
| 推送通知 | ✅ APNs | ❌（可走 Bark） |
| 数据归属 | 你的云 | 用户家 |
| 升级 | 自动 | `docker compose pull` |

**结论**：自部署是"功能 90% 对等 + 隐私 100%"，云端是"全功能 + 省心"，让用户自己选。

### 13.4 把自部署做好对 SaaS 的反哺价值

- 开源仓库 = 最便宜的获客渠道（GitHub trending + V2EX / Hacker News 曝光）
- 极客用户提的 issue 是产品改进的最高质量来源
- 自部署版本的存在让"数据隐私"成为产品的核心 trust signal — 用户知道自己**随时可以撤出**，反而更愿意留在云端
- 一旦自部署用户增长到一定规模，**双向同步**（自部署 ↔ 云端）可以做成 Pro+ 的高溢价功能

---

## 附录 A：技术选型对照表

| 层 | 选型 | 备选 | 不选 |
|----|------|------|------|
| 数据采集 | MediaCrawler + iOS Shortcut | XHS-Downloader | App 抓包 / 第三方书签 |
| OCR | PaddleOCR | 阿里读光 API | Tesseract / 自训练 |
| VLM | Qwen-VL-Max | 豆包 Vision Pro | GPT-4o (太贵) / 本地 7B (硬件门槛) |
| 结构化抽取 | Qwen JSON-Mode | GPT-4o (兜底重试) | — |
| Embedding | BGE-M3 (本地) | Conan-embedding (API) | OpenAI ada / 3-large |
| 存储（向量+BM25） | 自部署：SQLite + sqlite-vec；云端：Turbopuffer | LanceDB · Postgres+pgvector | Pinecone·Qdrant Cloud（过贵） |
| 结构化 + 认证（云端） | Supabase (Postgres + RLS) | Neon / Clerk + RDS | 自补贴认证 |
| 对象存储（云端） | Cloudflare R2 | S3 IA | 本机磁盘 |
| 密钥加密 | AWS KMS envelope | libsodium + master key in env | 明文 |
| 隐道（自部署手机访问） | Tailscale Funnel | Cloudflare Tunnel | ngrok（收费又不稳） |
| 认证（云端） | Supabase Auth (MVP) → Clerk（多人场景） | Better-Auth（完全掌控） | 自写 |
| 关键词检索 | SQLite FTS5 / Turbopuffer FTS | Tantivy | Elasticsearch（重） |
| 国内地点验证 | 高德 POI + 小红书反查 | 大众点评爬虫 (兜底) | 百度地图 |
| 海外地点验证 | Google Places (New) | Yelp (北美) | TripAdvisor (申请门槛高) |
| 前端 | Next.js (Web) → SwiftUI (iOS) | Tauri | 纯 CLI (Phase 1 之后) |

## 附录 B：参考链接

- MediaCrawler: https://github.com/NanmiCoder/MediaCrawler
- XHS-Downloader: https://github.com/JoeanAmier/XHS-Downloader
- sqlite-vec: https://github.com/asg017/sqlite-vec
- BGE-M3: https://huggingface.co/BAAI/bge-m3
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- Qwen Dashscope: https://dashscope.aliyuncs.com
- 高德 POI: https://lbs.amap.com/
- Google Maps Platform: https://mapsplatform.google.com/
- Turbopuffer: https://turbopuffer.com/
- Supabase RLS: https://supabase.com/docs/guides/database/postgres/row-level-security
- Tailscale Funnel: https://tailscale.com/kb/1223/tailscale-funnel
- AWS KMS Envelope Encryption: https://docs.aws.amazon.com/kms/latest/developerguide/concepts.html#enveloping
