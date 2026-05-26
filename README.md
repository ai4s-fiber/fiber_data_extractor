# 纤维材料文献数据提取软件 V6

线上多人协作 Web 系统，用于从纤维材料文献 PDF 中提取结构化数据。

## 架构

```
fiber_data_extractor_v6/
├── backend/          # FastAPI 后端
│   ├── app/
│   │   ├── api/      # 路由
│   │   ├── models/   # SQLAlchemy 模型
│   │   ├── schemas/  # Pydantic schemas
│   │   ├── services/ # 业务逻辑
│   │   ├── core/     # 配置、安全、依赖
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

# 设置环境变量（或创建 .env 文件）
# DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fiber_v6
# SECRET_KEY=your-secret-key
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
docker-compose up --build
```

## 用户角色

| 角色 | 说明 |
|---|---|
| admin | 管理员：管理项目、成员、API 配置、导出 |
| reviewer | 审核员/老师：审核候选行、修改、导出 |
| student | 学生：上传 PDF、启动抽取、修改候选、提交审核 |

## 40 列 Excel 导出

导出文件名：`数据主表.xlsx`，Sheet 名：`数据主表`，字段顺序固定 40 列。
