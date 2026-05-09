# Agent 角色配置

## 添加新 Agent

1. 在 `config/agents/{id}/agent/SOUL.md` 创建角色定义
2. 在 `config/feishu_credentials.json` 添加飞书凭证
3. 在 `config.py::agent_names` 和 `expertise` 添加映射
4. 在 `deploy/systemd/` 不需要改（模板 `%i` 自动匹配）

## SOUL.md 格式

```markdown
你是 {角色名} 智能体，负责 {项目} 的 {职责} 工作。

**核心职责**：
1. 职责一
2. 职责二

**严禁幻觉**：所有查询必须使用工具执行，禁止编造任何数据。

**对话人设**：以专业口吻回复。
```

## 当前 8 个角色

| ID | 名 | 角色 | 关键词 |
|---|---|---|---|
| zhugeliang | 诸葛亮 | 架构师 | 架构、设计、方案、review |
| liubei | 刘备 | PM | 汇总、项目、进度、管理 |
| guanyu | 关羽 | 后端 | 后端、java、api、接口 |
| zhaoyun | 赵云 | 前端 | 前端、vue、页面、组件 |
| xunyu | 荀彧 | DBA | 数据库、sql、表、性能 |
| zhangfei | 张飞 | QA | 测试、bug、禅道、验证 |
| huatuo | 华佗 | 产品 | 产品、需求、功能、prd |
| chenlin | 陈琳 | 文档 | 文档、手册、wiki、培训 |

## 意图路由

消息到达时，所有 Agent 的关键词匹配评分，分最高且 ≥2 分的 Agent 响应。广播消息（无明确目标）由 WS Listener 根据关键词自动路由到对应 Agent。
