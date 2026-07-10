# 纤维材料文献数据提取软件 V6

开放式本地/私有 Web 工作区，用于从纤维材料文献 PDF 中提取结构化数据。

## 架构

```
fiber_data_extractor_v6/
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
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 数据库 | PostgreSQL 16 |
| 后台任务 | Redis + RQ (预留) |
| 部署 | Docker Compose |
| 文件存储 | 本地磁盘 (可切换 MinIO/S3) |

## 快速启动（开发环境）

### 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 设置环境变量（或从仓库根目录复制 .env.example）
# DATABASE_URL=sqlite+aiosqlite:///./fiber_data.db
# MINERU_CLOUD_TOKEN=
# UPLOAD_DIR=./uploads

# 初始化数据库
python -m app.init_db

# 启动
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 http://localhost:5173，后端 API 在 http://localhost:8000。

### Docker Compose

```bash
cp .env.example .env
# 修改 .env 中的 POSTGRES_PASSWORD 后再启动
docker-compose up --build
```

## 使用模式

本项目现在作为开放工作区运行：没有登录页、用户账号、角色、成员管理或管理员专属页面。能够访问服务的人都可以管理项目、上传文献、启动抽取、复核候选记录、导出工作簿，以及配置项目级 LLM 参数。建议只在本地网络或受控的私有环境中部署。

## 40 列 Excel 导出

导出文件名：`数据主表.xlsx`，Sheet 名：`数据主表`，字段顺序固定 40 列。
