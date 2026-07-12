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
- LLM API Key（仅启动 AI 抽取时需要，在项目设置页填写）
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

### Docker Compose

```powershell
Copy-Item .env.example .env
# 修改 .env 中的 POSTGRES_PASSWORD 和 MINERU_CLOUD_TOKEN，再启动
docker compose up --build
```

Docker Compose 默认启用 PostgreSQL、Redis、后端和前端，前端入口为 http://localhost:3000。

## 功能配置

- 基础工作区功能（项目、文献上传、候选记录复核、Excel 导出）不需要登录。
- AI 抽取必须在项目设置页配置 `llm_provider`、`llm_base_url`、`llm_model` 和 `llm_api_key`。
- 默认 PDF 解析策略是 `mineru_cloud`，正式抽取使用 MinerU Cloud VLM 解析 PDF 版式、表格和结构。
- 必须在 `.env` 中配置 `MINERU_CLOUD_TOKEN` 后再启动抽取；未配置或 Cloud 解析失败时系统会直接失败并提示，不会自动退回本地 MinerU 或传统纯文本解析。
- `legacy` 解析链路仅保留给显式调试/快速离线验证使用，底层依赖 PyMuPDF 和 pdfplumber，不作为正式默认抽取体系。

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
$env:DASHSCOPE_API_KEY="你的 DashScope Key"
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
  --model qwen3.7-plus `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 `
  --model-mode weak `
  --parser-strategy mineru_cloud `
  --limit 3
```

降本增效相关默认值：

- `LLM_DISABLE_THINKING=true`：对 Qwen/DashScope 自动传 `enable_thinking=false`。
- `LLM_MAX_OUTPUT_TOKENS_PER_CALL=6000`：全局限制单次 LLM 输出预算。
- `WEAK_STAGE2_BATCH_SIZE=3`：弱模式将短文本块合批，表格仍单独抽取。
- `WEAK_STAGE2_BATCH_MAX_TOKENS=1800`：弱模式 Stage 2 单次输出预算上限。

## 使用模式

本项目现在作为开放工作区运行：没有登录页、用户账号、角色、成员管理或管理员专属页面。能够访问服务的人都可以管理项目、上传文献、启动抽取、复核候选记录、导出工作簿，以及配置项目级 LLM 参数。建议只在本地网络或受控的私有环境中部署。

## 40 列 Excel 导出

导出文件名：`数据主表.xlsx`，Sheet 名：`数据主表`，字段顺序固定 40 列。
