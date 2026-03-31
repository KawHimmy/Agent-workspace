# Agent-workspace

一个以 `FastAPI + LangGraph + MCP + Mem0 + Trigger.dev` 为核心的 ReAct Agent 工作台。

## 当前能力

- 邮箱注册/登录
- 会话式聊天
- Python LangGraph Agent，多节点执行链路
- Agent run trace / steps / events / tool logs
- MCP 文档、GitHub Insight、Knowledge Template 工具调用
- Mem0 长期记忆、本地兜底与记忆写回记录
- 文档上传与异步摘要
- 后台任务状态查看与重试
- PDF 论文中文结构化速读摘要
- Tool catalog、tool test、token / cost usage 追踪
- Workspace scope / 基础 RBAC
- Postgres / Supabase 可用时自动切换持久化
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


