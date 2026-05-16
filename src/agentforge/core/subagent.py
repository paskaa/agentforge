"""
SubAgent Pool — lightweight parallel task runners.

Each sub-agent handles ONE autonomous bug-fix task in its own thread,
sharing the parent's LLM client, tool registry, and Feishu connection.

This replaces the old pattern of enqueuing self_boot tasks to Redis
(which serialized them) with true parallel execution.
"""

import json
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from typing import Optional, Callable

from agentforge.core.tool_executor import run_script
from agentforge.core.bug_image import get_bug_images, describe_images
from agentforge.core.fix_trajectory import save_trajectory
from agentforge.core.test_env import get_tester

logger = logging.getLogger("agentforge.subagent")


class SubAgentContext:
    """Shared context passed to each sub-agent task."""

    def __init__(self, agent_id: str, agent_name: str, zentao_dir,
                 redis, redis_stream: str, reply_fn: Callable, refresh_fn: Callable,
                 zentao_write_bug: Callable, llm_fixer=None):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.zentao_dir = zentao_dir
        self.redis = redis
        self.redis_stream = redis_stream
        self.reply = reply_fn
        self.refresh_token = refresh_fn
        self.zentao_write_bug = zentao_write_bug
        self.llm_fixer = llm_fixer  # Optional LLMFixer for direct fix

    def z(self, name: str):
        return self.zentao_dir / name


class SubAgentPool:
    """Thread pool for parallel autonomous bug fixing."""

    def __init__(self, max_workers: int = 3):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="subagent")
        self._futures: list[Future] = []
        self._lock = threading.Lock()

    def submit(self, ctx: SubAgentContext, bug_id: str, bug_title: str,
               bug_reporter: str):
        """Submit a bug-fix task to the pool. Non-blocking."""
        future = self._executor.submit(
            _run_autonomous_fix, ctx, bug_id, bug_title, bug_reporter
        )
        with self._lock:
            self._futures.append(future)
        logger.info("[subagent:%s] Spawned for Bug #%s: %s", ctx.agent_id, bug_id, bug_title[:50])

    @property
    def active_count(self) -> int:
        with self._lock:
            self._futures = [f for f in self._futures if not f.done()]
            return len(self._futures)

    def shutdown(self, wait: bool = True):
        self._executor.shutdown(wait=wait)


# =========================================================================
#  Single task runner (runs in a thread)
# =========================================================================

def _run_autonomous_fix(ctx: SubAgentContext, bid: str, bug_title: str, bug_reporter: str):
    """Autonomous fix: query bug → Claude Code fix → commit → handoff to Zhangfei."""
    import time as _time
    start = _time.time()
    logger.info("[subagent:%s] Starting fix for Bug #%s: %s", ctx.agent_id, bid, bug_title[:50])

    # 0. Previously escalated bugs: allow re-fix (escalation no longer blocks)
    if _check_escalation(bid, ctx.agent_name, ctx):
        logger.info("[subagent:%s] Bug #%s was escalated but re-attempting with deep re-fix", ctx.agent_id, bid)

    # 1. Reproduce bug in local test environment
    try:
        tester = get_tester()
        if tester.login():
            repro = tester.reproduce_bug(bid, bug_title, "")
            logger.info("[subagent:%s] Bug #%s reproduction: %s", ctx.agent_id, bid, repro.get("description", "?"))
            image_desc = repro.get("description", "")
        tester.close()
    except Exception as e:
        logger.debug("[subagent:%s] Test env skip: %s", ctx.agent_id, e)

    # 2. Extract bug images (error messages often in screenshots)
    try:
        bug_images = get_bug_images(bid)
        image_desc = describe_images(bug_images, bid) if bug_images else ""
    except Exception as e:
        logger.debug("[subagent:%s] Image extraction skipped: %s", ctx.agent_id, e)
        bug_images = []
        image_desc = ""

    # 2. Notify Feishu
    ctx.reply(
        f"🔍 **子智能体启动**\n\n"
        f"Bug #{bid}：📌 **{bug_title}**\n👤 提出人: {bug_reporter}\n"
        f"{'📸 已提取 ' + str(len(bug_images)) + ' 张截图' if bug_images else ''}\n\n"
        f"正在分析修复..."
    )

    # 3. Try LLM direct fix first (free, fast), fallback to Claude Code
    fixed = False
    cout = ""
    cerr = ""
    crc = 1

    # QPM rate limiter: Redis SETNX distributed lock — only 1 Claude Code globally
    import time as _time
    _redis_lock_key = "claude_code_lock"
    _acquired = False
    for _attempt in range(600):  # Wait up to 10 minutes
        if ctx.redis.set(_redis_lock_key, ctx.agent_id, nx=True, ex=600):
            _acquired = True
            logger.info("[subagent:%s] Acquired Redis lock for Claude Code", ctx.agent_id)
            break
        _time.sleep(1)
    if not _acquired:
        logger.error("[subagent:%s] Redis lock timeout after 10 min", ctx.agent_id)
        ctx.reply(f"⚠️ Claude Code 分布锁等待超时，Bug #{bug_id} 稍后重试")
        return

    # Record task start in Redis status hash
    ctx.redis.hset("task:status", bug_id, json.dumps({
        "agent": ctx.agent_id, "bug_id": bug_id, "status": "running",
        "start": datetime.now().isoformat()[:19], "elapsed": "",
    }))
    ctx.redis.expire("task:status", 1800)  # Auto-expire after 30 min

    # LLM Fixer disabled — always use Claude Code directly
    llm_disabled = True
    if not llm_disabled and ctx.llm_fixer:
        logger.info("[subagent:%s] Trying LLM direct fix for Bug #%s", ctx.agent_id, bid)
        try:
            fix_steps = image_desc if image_desc else ""
            success, msg = ctx.llm_fixer.fix(bid, bug_title, fix_steps, ctx.agent_name)
            if success:
                ctx.reply(f"✅ **LLM 直修成功**\n\nBug #{bid} 已通过 qwen3-coder-plus 直接修复。\n📋 {msg}")
                fixed = True
                crc = 0
                cout = f"[LLM Fixer] {msg}"
                save_trajectory(bid, ctx.agent_name, "llm_fixer", True, _time.time() - start,
                                stdout=msg, fix_summary=msg)
            else:
                logger.info("[subagent:%s] LLM fix failed: %s, falling back to Claude Code",
                            ctx.agent_id, msg)
                save_trajectory(bid, ctx.agent_name, "llm_fixer", False, _time.time() - start,
                                stdout=msg, fix_summary=msg)
                ctx.reply(f"🔄 LLM 直修未成功（{msg}），切换 Claude Code...")
        except Exception as e:
            logger.warning("[subagent:%s] LLM fix exception: %s", ctx.agent_id, e)

    # Fallback: Claude Code
    if not fixed:
        try:
            # Add agent-specific guidance
            claude_title = bug_title
            if ctx.agent_id == "guanyu":
                claude_title += (
                    "\n\n**后端开发重点**：优先搜索 Java/Spring 后端代码。"
                    "关键词：Controller, Service, Mapper, API, 接口"
                )
            # Start a background thread to periodically flush Claude Code output to Redis
            import threading as _threading, io as _io
            _log_buf = _io.StringIO()
            def _pipe_to_redis():
                try:
                    with subprocess.Popen(
                        [ctx.z("claude-code-fix.sh"), bid, claude_title, ctx.agent_name],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10800,
                    ) as _p:
                        for _line in _p.stdout:
                            _log_buf.write(_line)
                            if _log_buf.tell() > 500:
                                ctx.redis.set(f"task:log:{bid}", _log_buf.getvalue()[-4000:], ex=600)
                    ctx.redis.set(f"task:log:{bid}", _log_buf.getvalue()[-8000:], ex=600)
                except Exception:
                    pass
            
            _log_thread = _threading.Thread(target=_pipe_to_redis, daemon=True)
            _log_thread.start()
            _log_thread.join(timeout=10800)
            
            cout = _log_buf.getvalue()
            crc = 1 if "⚠️ Claude Code 退出码: 1" in cout or "exit=1" in cout else 0
            cerr = ""
        except Exception as e:
            ctx.reply(f"⚠️ **修复异常**\n\nBug #{bid}: {e}")
            logger.error("[subagent:%s] Bug #%s fix exception: %s", ctx.agent_id, bid, e)
            return

    elapsed = _time.time() - start

    # Build a meaningful summary
    summary = "committed"
    if crc != 0:
        # Extract last meaningful error line from stdout/stderr
        err_lines = (cerr.strip() + "\n" + cout.strip()).split("\n")
        last_err = ""
        for line in reversed(err_lines):
            line = line.strip()
            if line and not line.startswith("=") and not line.startswith("-"):
                last_err = line[:120]
                break
        summary = f"exit={crc}: {last_err}" if last_err else f"exit={crc}"

    save_trajectory(bid, ctx.agent_name, "claude_code" if not fixed else "llm_fixer", crc == 0, elapsed,
                    stdout=cout, stderr=cerr,
                    fix_summary=summary)

    # Auto-retry on API quota failure
    if crc != 0 and "429" in cout:
        logger.warning("[subagent:%s] Bug #%s hit API quota, auto-retry in 5min", ctx.agent_id, bid)
        ctx.reply(f"⏳ Bug #{bid} 因 API 配额限制暂时失败，5 分钟后自动重试...")
        _time.sleep(300)
        # Release lock before re-enqueue
        try: ctx.redis.delete("claude_code_lock")
        except: pass
        # Re-enqueue
        ctx.redis.rpush(ctx.redis_stream, json.dumps({
            "agent_id": ctx.agent_id,
            "message": f"请修复 Bug #{bid}（API配额已恢复，自动重试）",
            "source": "pm_routed",
            "sender_id": "auto_retry",
            "chat_id": "", "is_dm": "true",
            "msg_id": f"auto-retry-{bid}-{int(_time.time())}",
            "timestamp": datetime.now().isoformat(),
        }))
        return

    # Write analysis comment to zentao (success or failure)
    try:
        analysis = _build_analysis_comment(ctx.agent_name, bid, bug_title, crc, elapsed, cout, cerr)
        run_script(ctx.z("zentao-write-bug.sh"), "comment", bid, analysis, timeout=15)
        logger.info("[subagent:%s] Wrote analysis comment to zentao Bug #%s", ctx.agent_id, bid)
    except Exception as e:
        logger.debug("[subagent:%s] Failed to write zentao comment: %s", ctx.agent_id, e)

    # 3. Verify result
    if crc == 0:
        # Check git log
        git_result = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            capture_output=True, text=True, timeout=10,
            cwd="/root/.openclaw/workspace/his-repo",
        )
        gcout = git_result.stdout

        if f"Fix Bug #{bid}" in gcout:
            # Build rich zentao comment
            changed_files = _get_changed_files()
            fix_summary = _extract_summary(cout)
            comment = (
                f"🤖 由 {ctx.agent_name} 通过 Claude Code 自动修复\n"
                f"耗时：{elapsed:.0f}s\n"
                f"修改文件：{changed_files}\n"
                f"修复摘要：{fix_summary}\n\n"
                f"📋 详细日志：\n{cout[:300]}"
            )
            # Post-fix verification: reload and check bug is gone
            try:
                tester = get_tester()
                if tester.login():
                    verif = tester.verify_fix(bid, [])
                    logger.info("[subagent:%s] Bug #%s post-fix verify: fixed=%s",
                                ctx.agent_id, bid, verif.get("fixed", False))
                tester.close()
            except Exception:
                pass

            # Mark as fixed (NOT resolved — resolution is Huatuo's job after testing)
            # Just add a comment to zentao, don't change status
            comment = (
                f"🤖 由 {ctx.agent_name} 通过 Claude Code 自动修复\n"
                f"耗时：{elapsed:.0f}s\n"
                f"修改文件：{changed_files}\n"
                f"修复摘要：{fix_summary}\n\n"
                f"📋 详细日志：\n{cout[:300]}"
            )
            try:
                run_script(ctx.z("zentao-write-bug.sh"), "comment", bid, comment, timeout=15)
            except Exception:
                pass

            ctx.reply(
                f"✅ **修复完成** ({elapsed:.0f}s)\n\n"
                f"Bug #{bid} 代码已修复并提交。\n\n"
                f"📋 **详细日志**：\n```\n{cout[:600]}\n```\n\n"
                f"🫡 流转给 **张飞** 进行回归测试..."
            )
            # Pipeline handoff
            ctx.redis.rpush(ctx.redis_stream, json.dumps({
                "agent_id": "zhangfei",
                "message": f"请测试 Bug #{bid} 的修复情况。提出人: {bug_reporter}。",
                "source": "pipeline_fix_done",
                "sender_id": ctx.agent_id,
                "bug_reporter": bug_reporter,
                "msg_id": f"pipeline-test-{bid}",
                "timestamp": datetime.now().isoformat(),
            }))
        else:
            ctx.reply(
                f"⚠️ **修复未确认** ({elapsed:.0f}s)\n\n"
                f"Bug #{bid} Claude Code 已执行但未找到提交记录。\n\n"
                f"📋 **输出**：\n```\n{cout[:400]}\n```\n"
                f"📋 **最近提交**：\n```\n{gcout[:200]}\n```"
            )
    else:
        reasons = {
            1: "无有效修改或执行错误",
            124: "执行超时",
            125: "Claude Code 启动失败",
            126: "脚本无执行权限",
            127: "Claude 命令未找到",
        }
        reason = reasons.get(crc, f"未知错误码 {crc}")
        ctx.reply(
            f"⚠️ **修复受阻** ({elapsed:.0f}s)\n\n"
            f"Bug #{bid} Claude Code 执行失败 ({reason})。\n\n"
            f"📋 **输出**：\n```\n{cout[:400]}\n```\n"
            f"📋 **错误**：\n```\n{cerr[:200]}\n```"
        )

    logger.info("[subagent:%s] Bug #%s done in %.0fs (exit=%d)",
                ctx.agent_id, bid, elapsed, crc)

    # Release Redis lock + update task status
    try:
        ctx.redis.delete("claude_code_lock")
        ctx.redis.hset("task:status", bug_id, json.dumps({
            "agent": ctx.agent_id, "bug_id": bug_id,
            "status": "done" if crc == 0 else "failed",
            "start": datetime.now().isoformat()[:19],
            "elapsed": f"{elapsed:.0f}s",
            "exit": crc,
        }))
        logger.info("[subagent:%s] Released Redis lock", ctx.agent_id)
    except Exception:
        pass

    # After failure, check if we just crossed the escalation threshold
    if crc != 0:
        _check_escalation(bid, ctx.agent_name, ctx)


def _build_analysis_comment(agent_name: str, bid: str, title: str,
                            exit_code: int, elapsed: float,
                            stdout: str, stderr: str) -> str:
    """Build a structured analysis comment for zentao."""
    status = "✅ 成功" if exit_code == 0 else "❌ 失败"
    method = "Claude Code" if "Claude" in (stdout or "") else "LLM Fixer"

    # Extract key findings from output
    findings = []
    output_text = (stdout or "") + (stderr or "")

    if "already been fixed" in output_text.lower() or "已在历史记录中修复" in output_text:
        findings.append("检测到该 Bug 已在 git 历史中修复")
    elif "dangerously-skip-permissions" in output_text:
        findings.append("Claude Code root 权限限制（已通过环境变量修复）")
    elif "429" in output_text:
        findings.append("API 配额不足，等待重试")
    elif "not supported" in output_text.lower():
        findings.append("模型不支持")
    elif "nothing to commit" in output_text.lower():
        findings.append("未找到需要修改的代码")
    elif exit_code != 0:
        findings.append(f"退出码 {exit_code}：可能需要后端修改或人工介入")

    # Extract the last meaningful line as summary
    lines = [l.strip() for l in output_text.split("\n") if l.strip()
             and not l.startswith("🤖") and not l.startswith("📋")
             and not l.startswith("===") and not l.startswith("---")]
    last_line = lines[-1][:150] if lines else "无有效输出"

    return (
        f"🤖 智能体修复分析\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"修复人：{agent_name}\n"
        f"方法：{method}\n"
        f"结果：{status}\n"
        f"耗时：{elapsed:.0f}s\n"
        f"发现：{'；'.join(findings) if findings else '参见详细日志'}\n"
        f"最后输出：{last_line}"
    )


def _check_escalation(bug_id: str, agent_name: str, ctx: object) -> bool:
    """
    Check if this bug should be escalated to manual intervention.
    Returns True if the bug should NOT be attempted again (escalated).
    """
    try:
        from agentforge.core.fix_trajectory import get_trajectories

        trajectories = get_trajectories(bug_id)
        if not trajectories:
            return False

        # Count failures per method
        failures = {}
        for t in trajectories:
            if not t.get("success", True):
                method = t.get("method", "unknown")
                failures[method] = failures.get(method, 0) + 1

        # Smart escalation: frontend/DBA failures → reroute to backend (guanyu)
        unique_methods = len(failures)
        total_failures = sum(failures.values())

        # Reroute rule: frontend or DBA agent failed 2+ times → send to guanyu
        reroute_agents = {"赵云", "荀彧", "zhaoyun", "xunyu"}  # Both Chinese and English names
        if (agent_name in reroute_agents or ctx.agent_id in reroute_agents) and total_failures >= 2:
            logger.warning("[subagent] Bug #%s rerouted: %s → guanyu (backend)", bug_id, agent_name)
            try:
                ctx.redis.rpush(ctx.redis_stream, json.dumps({
                    "agent_id": "guanyu",
                    "message": f"请修复 Bug #{bug_id}（{agent_name} 修复受阻，疑似后端问题，转关羽处理）",
                    "source": "rerouted_to_backend",
                    "sender_id": agent_name,
                    "chat_id": "",
                    "is_dm": "true",
                    "msg_id": f"reroute-{bug_id}-{int(time.time())}",
                    "timestamp": datetime.now().isoformat(),
                }))
                ctx.reply(
                    f"🔄 **Bug #{bug_id} 已转派**\n\n"
                    f"{agent_name} 修复 2 次未成功，疑似后端问题。\n"
                    f"已转派给 **关羽** 处理。"
                )
            except Exception as e:
                logger.error("[subagent] Reroute failed: %s", e)
            return True  # Signal: don't try again with current agent

        if unique_methods >= 2 and total_failures >= 3:
            logger.warning("[subagent] Bug #%s ESCALATED: %d methods, %d failures → manual intervention",
                           bug_id, unique_methods, total_failures)

            # Build detailed analysis from trajectory data
            details = ""
            for method, count in sorted(failures.items(), key=lambda x: -x[1]):
                # Get latest error for this method
                for t in trajectories:
                    if not t.get("success") and t.get("method") == method:
                        summary = t.get("fix_summary", "")[:100]
                        details += f"\n  {method}：{count} 次失败 — {summary}"
                        break

            msg = (
                f"🚨 **自动修复已达上限**\n\n"
                f"Bug #{bug_id} 已被 {unique_methods} 种方法共尝试 {total_failures} 次均失败。\n"
                f"失败详情：{details}\n\n"
                f"结论：该 Bug 疑似后端问题或需要深度业务分析，建议人工介入。"
            )
            try:
                ctx.reply(msg)
            except Exception as e:
                logger.error("[subagent] Failed to send escalation reply: %s", e)

            # Write escalation comment to zentao
            try:
                comment = (
                    f"🚨 智能体自动修复已达上限\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Bug #{bug_id} 经 {unique_methods} 种方法共 {total_failures} 次尝试均失败\n"
                    f"失败详情：{details}\n"
                    f"判定：疑似后端问题/需人工介入\n"
                    f"建议：后端开发人员检查权限配置或数据库查询\n"
                    f"标记人：{agent_name}"
                )
                from agentforge.core.tool_executor import run_script as _rs
                _rs(ctx.z("zentao-write-bug.sh"), "comment", bug_id, comment, timeout=15)
            except Exception as e:
                logger.error("[subagent] Failed to write escalation comment: %s", e)
            return True

        return False
    except Exception as e:
        logger.error("[subagent] Escalation check error: %s", e)
        return False


def _get_changed_files() -> str:
    """Get list of changed files from last git commit."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd="/root/.openclaw/workspace/his-repo",
        )
        lines = result.stdout.strip().split("\n")
        if lines:
            return lines[-1].strip() if lines else "无文件变更"
        return "无文件变更"
    except Exception:
        return "无法获取"


def _extract_summary(cout: str) -> str:
    """Extract a meaningful fix summary from Claude Code output."""
    if not cout:
        return "无可用的修复摘要"
    # Take the first meaningful line after Claude Code's header
    lines = [l.strip() for l in cout.split("\n") if l.strip() and not l.startswith("🤖") and not l.startswith("📋") and not l.startswith("✅") and not l.startswith("===") and not l.startswith("---")]
    for line in lines[:5]:
        if len(line) > 20:
            return line[:200]
    return cout[:200]
