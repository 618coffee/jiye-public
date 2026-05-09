# 小本本 · XHS Knowledge Base

把"小红书收藏夹"变成一个可用自然语言检索、按目的地聚合、随时间持续验证的个人旅行/美食/生活知识库。

> 当前为 Phase 1 + Phase 2 本地开发版（自部署形态）。详见 [DESIGN.md](./DESIGN.md)。

## 架构

```
backend/   FastAPI + SQLite (sqlite-vec + FTS5) + KnowledgeStore 抽象层
frontend/  Next.js 15 (App Router, PWA) + Tailwind + shadcn 风格 + framer-motion
data/      运行时数据：index.db + 原图缓存 + seed 样例
scripts/   一键启动 / 数据导入 / 索引重建
```

外部依赖采用**适配器 + Mock fallback** 设计：没有 Qwen API Key / 高德 API Key / 小红书 cookie 时自动落到 mock，UI 用 seed 数据立即能跑全流程；填上 Key 后 production 模式无缝接入。

## 一键本地启动

```bash
# 1) 安装依赖（首次）
./scripts/setup.sh

# 2) 启动 backend + frontend
./scripts/dev.sh

# 3) 浏览器打开
# http://localhost:3000
```

也可以分别启动：

```bash
# Backend (FastAPI on :8000)
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Frontend (Next.js on :3000)
cd frontend && npm run dev
```

## 配置（可选）

在 `backend/.env` 中填入（不填则走 Mock）：

```env
QWEN_API_KEY=sk-xxx        # 阿里 Dashscope
AMAP_API_KEY=xxx           # 高德 POI
GOOGLE_PLACES_API_KEY=xxx  # 海外 POI（可选）
```

## 核心功能

- 🗺️ **目的地工作台** — 输入"6月去京都"，自动聚合相关收藏，按必去/美食/咖啡/购物/拍照分组
- 🔍 **自然语言搜索** — "找我收藏过的、人均 200 以内、有露台的咖啡店"
- 📂 **我的收藏（inbox）** — 一键扫描小红书收藏夹，多标签自动聚类，人工筛选后升级进主库（详见 [docs/crawler.md](./docs/crawler.md) / [docs/clustering.md](./docs/clustering.md)）
- ✅ **按需鲜活度验证** — 选定城市才触发该城市 POI 的高德/Google 验证
- 🏷️ **AI 自动城市分类** — Ingest 时自动打 city/country/trip_purpose 标签
- 📂 **L1/L2 分级索引** — 文本默认全开，图片可选深度索引

## 开发笔记

- 数据访问统一走 `KnowledgeStore` 接口（见 `backend/app/store/base.py`），未来云端切 PolarDB+pgvector 时只换 driver
- 所有 LLM/OCR/POI 调用走 `backend/app/providers/`，每个 provider 都有 `MockProvider` 兜底
- 前端组件库自建（shadcn 风格 Tailwind），不引重型 UI 框架

## 文档

- [DESIGN.md](./DESIGN.md) — 整体架构与可行性方案
- [docs/crawler.md](./docs/crawler.md) — 小红书爬虫 / inbox 工作流
- [docs/clustering.md](./docs/clustering.md) — 多标签聚类逻辑与阈值
- [docs/embedding.md](./docs/embedding.md) — 为什么选 multilingual-e5-base + Apple GPU
