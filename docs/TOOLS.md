# 工具插件系统

## 添加新工具

只需写一个函数 + `@tool` 装饰器，放到 `core/builtin_tools.py`（或任何被 `discover_tools()` 扫描的模块）。

```python
from agentforge.core.tool_registry import tool, ToolContext
from agentforge.core.tool_executor import run_script

@tool(
    name="my_tool",              # 唯一标识
    description="描述",           # 文档用
    triggers=["触发词"],           # 精确触发（score +3）
    keywords=["关键词"],           # 模糊匹配（score +1）
    priority=10,                  # 越高越先执行
    raw_output=False,             # True = 绕过 LLM 直接输出
    agent_only=None,              # "liubei" = 仅刘备触发
)
def my_tool(message: str, ctx: ToolContext) -> Optional[str]:
    """返回 str 追加到 LLM 上下文，返回 None 表示不匹配。"""
    rc, out, err = run_script(ctx.z("脚本名.sh"), "参数", timeout=30)
    return out if out else None
```

## 当前工具列表

| 工具名 | 优先级 | RAW | 说明 |
|---|---|---|---|
| `zentao_bug_query` | 20 | ❌ | 查询禅道 Bug 详情（`#NNN`） |
| `liubei_triage` | 15 | ✅ | 刘备分派会议/任务 |
| `zentao_bug_summary` | 14 | ✅ | 汇总所有未解决 Bug |
| `zentao_bug_fix` | 12 | ❌ | 修复/解决/关闭 Bug |
| `zentao_my_bugs` | 11 | ✅ | 查询我的 Bug / 进度 |
| `git_status` | 10 | ❌ | Git 代码状态 |
| `git_commit` | 10 | ❌ | Git 提交代码 |
| `git_push` | 10 | ❌ | Git 推送代码 |

## 触发机制

每种触发词权重不同：
- `triggers` 匹配：score **+3**（精确意图）
- `keywords` 匹配：score **+1**（模糊相关）
- `agent_only` 过滤：非目标 Agent 直接跳过
- 按 score 降序执行，RAW 工具命中后立即返回

## 安全

所有脚本调用通过 `tool_executor.run_script()`，用户数据作为 `argv` 传入，绝不拼接进 shell 字符串。
