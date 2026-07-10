# MinerU 服务启动说明（Windows）

## 重要：中文路径问题

Windows 下如果项目路径含中文字符（如 `E:\项目\AI4S文献提取`），MinerU 依赖的 **fasttext** 模块会无法加载语言检测模型，导致启动失败。

**解决方案**：通过 ASCII 路径的 directory junction 启动 MinerU。

## 一次性准备（仅需做一次）

```powershell
# 创建 junction（映射中文路径到纯 ASCII 路径）
mklink /J C:\Users\Administrator\mineru_env_link "E:\项目\AI4S文献提取\.venv-mineru"
```

## 启动 MinerU 服务

```powershell
# 激活 MinerU 虚拟环境（通过 ASCII junction）
C:\Users\Administrator\mineru_env_link\Scripts\activate

# 启动 MinerU API 服务
magic-pdf-server --port 8001
```

MinerU 服务默认监听 `http://127.0.0.1:8001`。

## 验证服务可用

```powershell
curl http://127.0.0.1:8001/health
```

## 配置说明

后端通过以下环境变量/配置连接 MinerU：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MINERU_API_URL` | `http://127.0.0.1:8001` | MinerU API 地址 |
| `MINERU_BACKEND` | `pipeline` | 解析后端 |
| `MINERU_PARSE_METHOD` | `auto` | 解析方法 |
| `MINERU_LANG` | `en,zh` | 语言列表 |
| `MINERU_TASK_TIMEOUT_SECONDS` | `600` | 任务超时（秒） |
| `MINERU_POLL_INTERVAL_SECONDS` | `2` | 轮询间隔（秒） |

## 代理注意

后端 `mineru_client.py` 已设置 `trust_env=False`，避免本地 `127.0.0.1` 请求被系统代理劫持。

## 三个服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| MinerU | 8001 | PDF 解析服务 |
| FastAPI 后端 | 8000 | 应用后端 |
| Vite 前端 | 5173 | 开发前端 |
