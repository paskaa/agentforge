# AgentForge — Harness Engineering

> 模型决定上限，Harness 决定底线。Agent = Model + Harness。

## 快速导航

| 你要做什么 | 去哪里 |
|---|---|
| 了解项目架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 添加新工具插件 | [docs/TOOLS.md](docs/TOOLS.md) — `@tool` 装饰器 + 注册 |
| 新增 Agent 角色 | [docs/AGENTS.md](docs/AGENTS.md) — SOUL.md + 配置 |
| 理解 Bug 修复管线 | [docs/PIPELINE.md](docs/PIPELINE.md) — Fix → Test → Verify |
| 故障排查 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |
| 部署上线 | [docs/DEPLOY.md](docs/DEPLOY.md) |

## 铁律（不可违反）

1. **所有配置走 `Config` dataclass**，禁止裸写 `os.environ.get()`
2. **用户数据永远不做 shell 字符串拼接**，用 `subprocess.run(list)` 
3. **`__RAW__` 标记的输出绕过 LLM**，直接发给用户
4. **ACK 用 Redis stream ID（`_redis_id`）**，不是飞书消息 ID
5. **改 executor.py 后必跑 `venv/bin/python3 tests/test_core.py`**

## 知识库结构

```
agentforge/
├── AGENTS.md              ← 你在这里（地图入口，~100 行）
├── README.md              ← 项目概览、快速开始
├── docs/                  ← 详细文档（按需加载）
│   ├── ARCHITECTURE.md    ← 六层架构 + 模块关系
│   ├── TOOLS.md           ← 工具插件系统
│   ├── PIPELINE.md        ← Bug 修复管线
│   ├── AGENTS.md          ← Agent 角色配置
│   ├── TROUBLESHOOTING.md ← 故障排查
│   └── DEPLOY.md          ← 部署指南
├── src/agentforge/        ← 源码
├── config/                ← 配置（SOUL.md, gateway, credentials）
├── tests/                 ← 测试
└── deploy/systemd/        ← systemd 模板
```

## 过往教训

以下每条对应一个历史失败案例。Agent 犯过一次，就加一条规则：

- `#ACK_BUG_20260508` — `_redis_id` 不能被飞书 `msg_id` 覆盖，否则 ACK 死循环
- `#IMPORT_AUTOIMPORT` — Vue 项目有 `unplugin-auto-import`，不要手动改 import
- `#LIUBEI_SKIP_BOOT` — 刘备（PM）不修 Bug，boot_check 跳过
- `#COMMAND_INJECTION` — subprocess 用 list args，不用 shell=True 拼接
- `#FEISHU_CURL` — 飞书发消息用 `requests`，不用 `curl + tempfile`
- `#CONFIG_SCATTER` — 所有模块统一用 `Config` dataclass
- `#PERMISSION_GRACEFUL` — 后端权限导致的报错用优雅降级（msgError→console.warn），不要死磕业务逻辑
- `#ENV_INLINE_COMMENT` — .env 不支持行内注释，`KEY=value # comment` 会把注释当值

## Harness 成熟度

| 层级 | 组件 | 状态 | 说明 |
|---|---|---|---|
| L1 信息边界 | AGENTS.md + docs/ | ✅ | 地图式文档，渐进披露 |
| L2 工具系统 | @tool registry | ✅ | 8 个插件，声明式注册 |
| L3 执行编排 | pipeline + workflow | ✅ | Fix→Test→Verify 管线 |
| L4 记忆与状态 | ExperienceMemory + sessions | ✅ | fcntl 锁，10 条历史 |
| L5 评估与观测 | metrics.py + gates | ✅ | Prometheus 格式，3 门禁 |
| L6 约束与恢复 | dead_letter + guard rules | ✅ | 3 次重试后 DLQ |

## 门禁

每次改动自动跑：`check` → `test` → `custom`（见 `.deepseek/config.toml [gates]`）
