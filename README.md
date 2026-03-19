# Agent-workspace

一个以 `FastAPI + LangGraph + MCP + Mem0 + Trigger.dev` 为核心的 ReAct Agent 工作台。

## 当前能力

- 邮箱注册/登录
- 会话式聊天
- Python LangGraph Agent
- MCP 文档工具调用
- Mem0 长期记忆与本地兜底
- 文档上传与异步摘要
- PDF 论文中文结构化速读摘要
- 原生 `HTML + CSS + JS` 前端工作台

## 项目结构

- `apps/api_py`: Python 主后端
- `apps/auth`: Better Auth sidecar
- `apps/web/static`: 前端静态页面
- `packages/mcp-servers`: MCP 工具服务
- `src/trigger`: Trigger.dev 任务
- `supabase`: 数据库迁移

## 本地启动

1. 安装 Python 依赖
2. 安装 Node.js 依赖
3. 配置 `.env`
4. 运行：

```bash
npm start
```

默认前端入口：

```text
http://localhost:3000
```

## 安全说明

仓库默认不提交以下内容：

- `.env` 和实际密钥
- `langgraph入门/`、`Mem0/` 中的本地密钥文件
- `storage/` 上传文件
- `data/` 本地运行数据
- `node_modules/`、`.trigger/`、Python 缓存
