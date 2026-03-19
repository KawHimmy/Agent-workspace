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



## 安全说明

仓库默认不提交以下内容：
- `.env` 和实际密钥
- `storage/` 上传文件
- `data/` 本地运行数据
- `node_modules/`,`.trigger/`,Python 缓存
