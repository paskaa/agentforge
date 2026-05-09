# AgentForge 六层 Harness 架构

## L1 — 信息边界层

**职责**：Agent 该知道什么、不该知道什么。

- `config.py::Config` — 全局配置数据中心
- `config/agents/{id}/agent/SOUL.md` — 每个 Agent 的角色定义（简洁，非百科全书）
- `AGENTS.md` — 地图入口，指向深层文档
- 意图路由：`executor.should_respond()` 通过关键词评分决定哪个 Agent 响应

## L2 — 工具系统层

**职责**：Agent 怎么跟外部世界交互。

- `core/tool_registry.py` — 插件注册框架 + `@tool` 装饰器
- `core/builtin_tools.py` — 8 个内置工具（Bug 查询、Git、汇总等）
- `core/tool_executor.py` — 安全的 subprocess 封装（防命令注入）
- 工具选择：关键词触发 + 优先级排序

## L3 — 执行编排层

**职责**：多步骤任务怎么串起来。

- `core/pipeline.py` — Bug 修复管线（Fix → Test → Verify）
- `workflow/engine.py` — 通用工作流引擎（串行/并行/审批）
- 自驱模式：`boot_check()` → 扫描 Bug → Claude Code 异步修复

## L4 — 记忆与状态层

**职责**：长任务中间结果怎么管。

- `core/memory.py` — ExperienceMemory（fcntl 文件锁，线程安全）
- `core/llm.py::SessionManager` — 对话历史（保留最近 10 条）
- Redis Stream — 跨 Agent 消息队列（PENDING 优先）
- `self_optimizer.py` — 动态规则积累（`.dynamic_rules.md`）

## L5 — 评估与观测层

**职责**：Agent 怎么知道自己做对了没有。

- `core/metrics.py` — Prometheus 格式指标（计数器/量表/延迟）
- `self_optimizer._self_evaluate()` — LLM 自评 1-5 分
- `.deepseek/config.toml [gates]` — 3 道门禁（check/test/custom）
- `tests/test_core.py` — 17 个单元测试

## L6 — 约束、校验与恢复层

**职责**：出错了怎么办。

- `core/dead_letter.py` — 死信队列（3 次重试后移入 DLQ，可重放）
- `executor.ack()` — 消息确认（`_redis_id` 防覆盖）
- `LLMClient` — 指数退避重试（3 次）
- 刘备 boot_check 跳过 — 防 PM 乱修 Bug
- `__RAW__` 反幻觉 — 工具结果绕过 LLM 直接输出
