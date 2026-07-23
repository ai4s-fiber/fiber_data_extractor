# Fiber Data Extractor

开放式本地/私有 Web 工作区，用于从纤维材料文献 PDF 中提取结构化数据。

> 当前代码仓库已公开用于协作查看。应用已取消登录和用户系统，能够访问部署服务的人都可以操作项目数据；实际部署请只放在本机、内网或受控环境中。

## 架构

```
fiber_data_extractor/
├── backend/          # FastAPI 后端
│   ├── app/
│   │   ├── api/      # 路由
│   │   ├── models/   # SQLAlchemy 模型
│   │   ├── schemas/  # Pydantic schemas
│   │   ├── services/ # 业务逻辑
│   │   ├── core/     # 配置、数据库、资源依赖
│   │   └── main.py
│   ├── alembic/      # 数据库迁移
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/         # React + TypeScript + Vite
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/
│   │   ├── stores/
│   │   └── App.tsx
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React + TypeScript + Vite + Ant Design |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 数据库 | PostgreSQL 16 |
| 后台任务 | 进程内队列 + 可选 Redis 进度/缓存 |
| 部署 | Docker Compose |
| 文件存储 | 本地磁盘 |

## 前置条件

- Python 3.11
- Node.js 20.19+
- npm 10+
- Docker Desktop（可选，仅 Docker Compose 部署需要）
- LLM API Key（仅启动 AI 抽取时需要，在项目设置页填写；默认 OpenAI-compatible 模型为 `gpt-5.5`）
- `MINERU_CLOUD_TOKEN`（正式抽取默认使用 MinerU Cloud；不配置时抽取会失败并提示）

## 获取代码

```bash
git clone https://github.com/ai4s-fiber/fiber_data_extractor.git
cd fiber_data_extractor
```

## 快速启动（开发环境）

### 后端

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 可选：从仓库根目录复制环境模板
Copy-Item ..\.env.example .env

python -m app.init_db
uvicorn app.main:app --reload --port 8000
```

### 前端

另开一个终端：

```powershell
cd frontend
npm ci
npm run dev
```

前端默认运行在 http://localhost:5173，后端 API 在 http://localhost:8000。

开发模式默认使用 SQLite，本地数据库和上传/导出产物会生成在 `backend/` 下，并已被 `.gitignore` 忽略。

### 数据库初始化与升级

- 新克隆或全新数据库直接运行 `python -m app.init_db`。该命令会创建完整表结构、补齐运行时索引，完成后主动释放连接并正常退出。
- 升级已有数据库前先备份数据库，再在 `backend/` 下运行 `alembic upgrade head`；随后照常执行 `python -m app.init_db` 做幂等的运行时结构检查。
- 不要对空数据库直接运行 `alembic upgrade head`：仓库的首个 Alembic revision 是既有部署的基线标记，不负责从零创建全量表结构。

### Docker Compose

```powershell
Copy-Item .env.example .env
# 修改 .env 中的 POSTGRES_PASSWORD 和 MINERU_CLOUD_TOKEN，再启动
docker compose up --build
```

Docker Compose 默认启用 PostgreSQL、Redis、后端和前端，前端入口为 http://localhost:3000。

## 功能配置

- 基础工作区功能（项目、文献上传、候选记录复核、Excel 导出）不需要登录。
- AI 抽取必须在项目设置页配置 `llm_provider`、`llm_base_url`、`llm_model` 和 `llm_api_key`。默认值为 `openai` / `https://aigw.sotatts.online/v1` / `gpt-5.5`。
- 默认 PDF 解析策略是 `mineru_cloud`，正式抽取使用 MinerU Cloud VLM 解析 PDF 版式、表格和结构。系统会保存 MinerU Markdown/JSON 产物；同一文献、同一解析配置重新抽取时会优先复用产物，避免重复消耗 MinerU 配额。
- 必须在 `.env` 中配置 `MINERU_CLOUD_TOKEN` 后再启动抽取；未配置或 Cloud 解析失败时系统会直接失败并提示，不会自动退回本地 MinerU 或传统纯文本解析。
- 可选解析策略 `mineru_local_sync` 使用 MinerU 官方本地 `mineru-api /file_parse` 同步接口；适合单篇兼容调用。自建 GPU 批处理优先使用 MinerU 异步 `/tasks` 与 `mineru-router`，避免同步请求长期占用连接。默认仍使用 MinerU Cloud。
- `legacy` 解析链路仅保留给显式调试/快速离线验证使用，底层依赖 PyMuPDF 和 pdfplumber，不作为正式默认抽取体系。
- 默认批量并发预算：`EXTRACTION_MAX_CONCURRENT_JOBS=3`、`STRONG_LLM_PARALLEL_CALLS=4`，批量抽取最多占用 12 路 LLM 调用；`LLM_GLOBAL_MAX_CONCURRENT_CALLS=16` 为进程内总闸门，固定预留 4 路给日常交互/测试。
- `EXTRACT_REVIEW_ARTICLES=false`：默认跳过标题或首页被高置信度识别为综述的文献，避免把其引用论文数据混入本论文结果并节省 LLM Token；需要专门挖掘综述时再显式开启。
- MinerU 已解析出的简单结构化性能表会走确定性行列映射，不再重复交给 LLM；复杂或未覆盖表格只使用紧凑的表格专用提示回退到 GPT，不会重跑整篇 Stage 2。
- 数值、单位和样品绑定均要求原文证据；均值旁存在唯一的 `±`/括号标准差时会零 Token 恢复到结构化条件，歧义值保留原样并进入现有质量检查。
- 最终样品卡、事实、候选记录和证据在一个数据库事务内替换。重跑失败、超时或取消时保留上一次完整结果，成功提交后才切换到新结果。

## 验证

后端：

```powershell
cd backend
pytest tests\test_config.py tests\test_health.py tests\test_open_workspace_contract.py -q
```

前端：

```powershell
cd frontend
npm ci
npm run build
```

## 抽取性能 Benchmark

仓库包含可选 benchmark 脚本，用于在本地用公开 PDF 对抽取速度、质量覆盖和 LLM token 使用做回归验证。PDF、报告、SQLite benchmark 数据库均被 `.gitignore` 忽略，不应提交到 Git。

PowerShell 示例：

```powershell
cd backend
$env:AIGW_API_KEY="你的 AI Gateway Key"
$env:DATABASE_URL="sqlite+aiosqlite:///./benchmark.db"
$env:ALLOW_SQLITE_FALLBACK="true"
$env:REDIS_ENABLED="false"
# 在 .env 中填入 MINERU_CLOUD_TOKEN
$env:MINERU_CLOUD_TRUST_ENV="true"
$env:DEFAULT_PARSER_STRATEGY="mineru_cloud"
$env:MINERU_CLOUD_FALLBACK_LOCAL="false"
$env:MINERU_FALLBACK_LEGACY_PARSER="false"
$env:LLM_DISABLE_THINKING="true"
$env:LLM_METRICS_LOCAL_ENABLED="true"
$env:LLM_METRICS_DIR="./reports/llm_metrics"

python scripts\benchmark\run_extraction_benchmark.py `
  --pdf-dir benchmark_pdfs `
  --api-key-env AIGW_API_KEY `
  --model gpt-5.5 `
  --base-url https://aigw.sotatts.online/v1 `
  --model-mode strong `
  --parser-strategy mineru_cloud `
  --limit 3
```

## 工程批量抽取

`run_bulk_extraction.py` 面向数千篇本地 PDF。它先按 SHA-256 去重，再使用 MinerU Cloud 官方 v4 批量上传接口预解析；每篇完成后立即写入共享缓存并进入 `gpt-5.5` 强抽取队列，不会等待同批最慢文献。应用任务和 MinerU 远端批次都会持久化，同一命令中断后重跑即可续传、续查和跳过已完成文献。

运行批量命令时，不要同时启动另一个连接同一数据库的后端抽取 worker：

```powershell
cd backend
$env:AIGW_API_KEY="你的 AI Gateway Key"
$env:MINERU_CLOUD_TOKEN="你的 MinerU Token"

# 全量提交前先做本地清单、PDF 限制、去重、磁盘和相关性预检
python scripts\ops\run_bulk_extraction.py `
  --pdf-dir "E:\数据\创智的paper" `
  --dry-run `
  --report-dir ".\reports\bulk-preflight"

# 预检报告确认后启动正式抽取
python scripts\ops\run_bulk_extraction.py `
  --pdf-dir "E:\数据\创智的paper" `
  --project-name "Fiber corpus" `
  --model gpt-5.5 `
  --batch-size 20 `
  --max-jobs 3
```

- 默认使用硬链接把语料纳入 `uploads/`，源目录与项目不在同一磁盘时自动退回复制；不会修改原始 PDF。
- 默认启用保守型本地相关性预筛。它只用成熟的 `pypdf` 读取元数据和前两页，经过 Unicode 规范化后跳过明确综述、书评、勘误、撤稿和纯临床噪声；仅仅“前两页未出现纤维关键词”不会被跳过，文本不足或判断模糊的论文仍提交 MinerU。报告中的 `relevance_skipped_files` 可逐篇审计，使用 `--include-prefilter-rejected` 可强制纳入。
- 默认跳过已完成和失败文献，并以每篇论文最新一条任务记录为准。确认要重试最新失败项时增加 `--retry-failed`，确认要重新抽取最新已完成项时增加 `--reextract-completed`；旧成功记录不会再阻止最新失败任务重试。
- 使用一个或多个 `--pdf-name "exact-name.pdf"` 可以只恢复指定论文，不需要重新扫描和提交整个项目。
- 运行摘要写入 `backend/reports/bulk/bulk_project_<id>_summary.json`；其中 `result_summary` 按论文列出任务状态、样品/事实/候选数、QA 标记、缺失证据和精确重复。`healthy=false` 表示存在工程失败、任务记录丢失、证据缺失或精确重复；`quality_gate_passed=false` 还可能表示意外空结果、模型/解析器配置漂移或需要人工复核的保守 QA 结果。报告、PDF、数据库和解析缓存都已被 `.gitignore` 排除。
- 带有明确“previously reported / literature / prior work”等引用语言、且没有当前工作结果信号的外部数值保留在 `FactCandidate` 审计层，不进入最终候选记录；仅凭章节标签不会删除数据，避免版面章节误标造成真实结果丢失。
- PostgreSQL 启动时会把旧版抽取自由文本列自动扩展为 `TEXT`；部署更新时仍建议先执行 `alembic upgrade head`。SQLite 本身不执行 `VARCHAR(n)` 长度限制。
- MinerU Cloud 官方限制为每批最多 200 个文件、每账号每天最多 10,000 个文件；每天前 2,000 页享受最高处理优先级，超过后仍可处理但优先级会降低。项目默认每批 20 个，以降低超长尾文献和整批重试的影响；不要为了追求提交速度直接使用 200。
- 官方参考：[Precision Extract API](https://mineru.net/doc/docs/index_en/)、[API 速率限制](https://mineru.net/doc/docs/limit_en/)、[本地 CLI 与后端参数](https://opendatalab.github.io/MinerU/usage/cli_tools/)、[MinerU 源码](https://github.com/opendatalab/MinerU)。

降本增效相关默认值：

- `MINERU_REUSE_PARSE_ARTIFACTS=true`：复用已完成的 MinerU Cloud/本地产物，重新抽取只跑 LLM 结构化阶段。
- `MINERU_CLOUD_IS_OCR=false`：数字版 PDF 默认不强制 OCR，减少不必要的云端耗时；扫描件可手动改为 `true`。
- `MINERU_CLOUD_ENABLE_TABLE=true`：表格对材料数据抽取很关键，默认开启；仅做纯文本预筛时可关闭以换取速度。
- `MINERU_HYBRID_EFFORT=medium`：本地 Hybrid MinerU 后端默认使用官方推荐的速度/精度平衡档。
- `MINERU_CLOUD_BATCH_SIZE=20`：官方批量上传单批大小；上限 200，默认使用更稳健的小批次。
- `MINERU_CLOUD_UPLOAD_CONCURRENCY=8`：签名 URL 流式上传并发，上传过程不会把整批 PDF 同时读入内存。
- `MINERU_CLOUD_MAX_RETRIES=4`：对 HTTP 429/5xx、模型服务暂不可用和任务队列满做带抖动的指数退避。
- `LLM_GLOBAL_MAX_CONCURRENT_CALLS=16`：进程内所有 LLM 请求共享总并发闸门。
- `LLM_BATCH_MAX_CONCURRENT_CALLS=12`：批量文献抽取的并发上限；实际预算还会受全局上限和预留通道约束。
- `LLM_INTERACTIVE_RESERVED_CALLS=4`：从全局 LLM 并发中显式预留日常调用通道；系统会在保证已启动任务至少可前进的前提下压缩批量预算。
- `STRONG_HOLISTIC_PERFORMANCE_WINDOW_CHARS=6000`：按 MinerU 块边界切分 Results，并行调用 `gpt-5.5`，降低大窗口长尾超时。
- `STRONG_HOLISTIC_PERFORMANCE_TIMEOUT_SECONDS=180`：单个窗口超时后按 MinerU 块缩小重试；仍失败时继续走定向 Stage 2 补抽，最终证据与质量门禁仍不通过时才把论文标记为需处理。
- `STRONG_HOLISTIC_PARALLEL_CALLS=3`：强模式 Holistic 分支最多并行 3 路；样品目录完成后，背景与性能窗口并行执行。
- `STRONG_HOLISTIC_BACKGROUND_TIMEOUT_SECONDS=60`：组成/工艺背景支路采用短超时；超时只降低背景字段覆盖率，不阻塞性能主链路。
- `STRONG_TABLE_LLM_TIMEOUT_SECONDS=75`：结构化表格请求使用独立超时；首轮失败后仅将未覆盖表格交给 Stage 2 重试。
- `EXTRACTION_MAX_ATTEMPTS=2`：仅对可恢复的网络、限流和上游超时错误重试；确定性配置/数据错误直接失败，避免无效消耗 Token。
- `EXTRACTION_PIPELINE_TIMEOUT_SECONDS=1800`：单篇任务总看门狗；超时回滚当前事务并保留旧结果。
- `LLM_MAX_OUTPUT_TOKENS_PER_CALL=6000`：全局限制单次 LLM 输出预算。
- `WEAK_STAGE2_BATCH_SIZE=3`：弱模式将短文本块合批，表格仍单独抽取。
- `WEAK_STAGE2_BATCH_MAX_TOKENS=1800`：弱模式 Stage 2 单次输出预算上限。

## 使用模式

本项目现在作为开放工作区运行：没有登录页、用户账号、角色、成员管理或管理员专属页面。能够访问服务的人都可以管理项目、上传文献、启动抽取、复核候选记录、导出工作簿，以及配置项目级 LLM 参数。建议只在本地网络或受控的私有环境中部署。

## 40 列 Excel 导出

Web 导出和批量导出统一使用 `Main_Data`、`Papers`、`Evidence`、`Parse_Blocks`、`Quality_Report`。`Main_Data` 固定 40 列，包含文献元数据、样品、成分、工艺、结构、性能和核心证据文本，可独立交付；其余 Sheet 提供细粒度溯源。导出是非破坏操作，不会清除数据库候选记录。
Web 单次导出对超大项目设有内存保护；超过 200 篇、5 万条候选或 25 万个解析块时应使用下面的逐篇批量导出脚本。

全量工程建议按论文生成工作簿，脚本会原子写入、写后校验并按数据库内容签名续传：

```powershell
cd backend
python scripts\ops\export_project_workbooks.py `
  --project-id 1 `
  --database-url "postgresql+asyncpg://..." `
  --output-dir "E:\fiber_exports"
```

重复运行会校验并跳过未变化的工作簿；增加 `--overwrite` 可强制重建。超大证据或解析块会自动拆分到编号 Sheet，避免超过 Excel 单 Sheet 行数上限。完整原始文件名始终保留在 `Papers.original_filename`，磁盘文件名只做 Windows 安全截断。
