"""
Built-in tools — all agentforge tools as decorated plugins.

Add new tools by writing a function + @tool decorator.
No need to touch executor.py.

Each tool receives (message, ctx: ToolContext) and returns:
  - str  → appended to results (shown to LLM as context)
  - None → no match (skip)

Set raw_output=True in the decorator to bypass LLM entirely.
"""

import re
from pathlib import Path
from typing import Optional

from agentforge.core.tool_registry import tool, ToolContext
from agentforge.core.tool_executor import run_script


# =========================================================================
#  Bug Query — detects #NNNN patterns and queries zentao
# =========================================================================

@tool(
    name="zentao_bug_query",
    description="查询禅道 Bug 详情",
    triggers=["#"],
    keywords=["bug", "禅道", "缺陷", "查询"],
    priority=20,
)
def zentao_bug_query(message: str, ctx: ToolContext) -> Optional[str]:
    bug_ids = list(set(re.findall(r"#?(\d{2,4})", message)))
    if not bug_ids:
        return None
    parts = []
    for bid in bug_ids:
        rc, out, err = run_script(ctx.z("zentao-bug-query.sh"), bid, timeout=15)
        if rc == 0 and out:
            parts.append(f"【禅道查询结果】Bug #{bid}\n{out}")
        else:
            parts.append(f"【查询结果】Bug #{bid} 不存在或查询失败")
    return "\n\n".join(parts) if parts else None


# =========================================================================
#  Liubei Triage — meeting / assignment dispatch
# =========================================================================

@tool(
    name="liubei_triage",
    description="刘备分派会议/任务",
    triggers=["会议", "分配", "分派", "制定方案"],
    keywords=["组织"],
    priority=15,
    raw_output=True,
    agent_only="liubei",
)
def liubei_triage(message: str, ctx: ToolContext) -> Optional[str]:
    # Only fires for liubei + "组织" present
    if "组织" not in message:
        return None
    rc, out, _ = run_script(ctx.z("liubei_triage.sh"), timeout=60)
    return out if out else None


# =========================================================================
#  Bug Summary — "汇总所有未解决 Bug" etc.
# =========================================================================

@tool(
    name="zentao_bug_summary",
    description="汇总所有未解决 Bug",
    triggers=["汇总", "所有", "未解决", "汇报进度", "修复进度", "整体情况"],
    keywords=["bug"],
    priority=14,
    raw_output=True,
)
def zentao_bug_summary(message: str, ctx: ToolContext) -> Optional[str]:
    if "bug" not in message.lower():
        return None
    rc, out, _ = run_script(ctx.z("zentao-all-bugs.sh"), "50", timeout=30)
    return out if out else None


# =========================================================================
#  Git Status
# =========================================================================

@tool(
    name="git_status",
    description="Git 代码状态",
    triggers=["git status", "代码状态"],
    keywords=[],
    priority=10,
)
def git_status(message: str, ctx: ToolContext) -> Optional[str]:
    rc, out, _ = run_script(ctx.z("git-ops.sh"), "status", timeout=10)
    return out if out else "Git 状态查询无结果"


# =========================================================================
#  Git Commit
# =========================================================================

@tool(
    name="git_commit",
    description="Git 提交代码",
    triggers=["git commit", "提交代码", "commit"],
    keywords=[],
    priority=10,
)
def git_commit(message: str, ctx: ToolContext) -> Optional[str]:
    commit_msg = "智能体修复"
    if "message:" in message:
        commit_msg = message.split("message:", 1)[-1].strip()[:200]
    rc, out, _ = run_script(ctx.z("git-ops.sh"), "commit", commit_msg, timeout=30)
    return out if out else None


# =========================================================================
#  Git Push
# =========================================================================

@tool(
    name="git_push",
    description="Git 推送代码",
    triggers=["git push", "推送代码", "push"],
    keywords=[],
    priority=10,
)
def git_push(message: str, ctx: ToolContext) -> Optional[str]:
    rc, out, _ = run_script(ctx.z("git-ops.sh"), "push", timeout=30)
    return out if out else None


# =========================================================================
#  Bug Fix — query detail + prepare fix
# =========================================================================

@tool(
    name="zentao_bug_fix",
    description="修复/解决/关闭 Bug",
    triggers=["修复 bug", "解决 bug", "resolve bug", "关闭 bug"],
    keywords=["修复", "解决", "关闭"],
    priority=12,
)
def zentao_bug_fix(message: str, ctx: ToolContext) -> Optional[str]:
    bug_match = re.search(r"#?(\d+)", message)
    if not bug_match:
        return None
    bid = bug_match.group(1)

    if "修复" in message:
        rc, out, _ = run_script(ctx.z("zentao-bug-query.sh"), bid, timeout=15)
        if out:
            return f"【Bug #{bid} 详情】\n{out}\n【指令】请根据上述详情，分析原因并给出修复方案。"
        return None

    if any(kw in message for kw in ["解决", "resolve", "关闭"]):
        return f"⚠️ 权限不足：智能体无权关闭 Bug #{bid}。请人类发起人手动操作。"

    return None


# =========================================================================
#  My Bugs / Progress
# =========================================================================

@tool(
    name="zentao_my_bugs",
    description="查询我的 Bug / 进度",
    triggers=["我的任务", "进度", "汇报", "my bugs", "my tasks"],
    keywords=[],
    priority=11,
    raw_output=True,
)
def zentao_my_bugs(message: str, ctx: ToolContext) -> Optional[str]:
    rc, out, _ = run_script(
        ctx.z("zentao-my-bugs.sh"), ctx.agent_account, "active", timeout=30,
    )
    return out if out else None
