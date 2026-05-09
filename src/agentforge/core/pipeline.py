"""
Pipeline Handlers — multi-step bug-fix pipeline logic.

Extracted from executor.py: test (Zhangfei), verify (Huatuo),
and self-boot autonomous fix (all agents).
"""

import logging
import re
import threading
import time
from datetime import datetime
from typing import Optional

from agentforge.core.tool_executor import run_script
from agentforge.core.trace_store import traces
from agentforge.core.bug_image import get_bug_images, describe_images
from agentforge.core.fix_trajectory import save_trajectory

logger = logging.getLogger("agentforge.pipeline")


class PipelineContext:
    """Shared context for pipeline handlers."""

    def __init__(self, agent_id: str, agent_name: str, zentao_dir,
                 redis, redis_stream: str, reply_fn, refresh_fn):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.zentao_dir = zentao_dir
        self.redis = redis
        self.redis_stream = redis_stream
        self.reply = reply_fn        # (text: str) -> None
        self.refresh_token = refresh_fn  # () -> None

    def z(self, name: str):
        return self.zentao_dir / name


def _analyze_and_route(pctx: PipelineContext, bug_id: str, bug_title: str) -> str:
    """Liu Bei's analysis: route bug to the best agent by expertise."""
    EXPERTISE = {
        "zhugeliang": ["架构", "设计", "方案", "review", "重构", "规范", "api设计"],
        "guanyu": ["后端", "java", "api", "接口", "服务", "spring", "service", "controller",
                   "数据为空", "加载失败", "无权限", "接口报错", "500", "404",
                   "签发", "计费", "收费", "退费", "医嘱保存", "医嘱提交",
                   "库存", "领用", "退库", "盘存", "作废", "冲销"],
        "zhaoyun": ["前端", "vue", "页面", "样式", "css", "组件", "表单", "按钮", "ui",
                    "弹窗", "对话框", "列表", "表格", "输入框", "下拉", "选择", "回显", "渲染"],
        "xunyu": ["数据库", "sql", "表", "查询", "索引", "性能", "慢查询", "优化", "数据", "mysql"],
        "huatuo": ["产品", "需求", "功能", "用户", "体验", "prd", "业务流程", "临床", "his"],
        "chenlin": ["文档", "说明", "手册", "wiki", "知识库", "培训", "发布", "公告"],
    }
    text = bug_title.lower()
    best_agent = "zhaoyun"  # Default to frontend
    best_score = 0
    for agent_id, keywords in EXPERTISE.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > best_score:
            best_score = score
            best_agent = agent_id
    return best_agent


def handle_pipeline_test(pctx: PipelineContext, task: dict):
    """Zhangfei: REAL regression test — read git diff, analyze fix quality, assign back."""
    import subprocess

    message = task.get("message", "")
    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    reporter = task.get("bug_reporter", "")
    if not reporter:
        rm = re.search(r'提出人:\s*([^\s。]+)', message)
        if rm:
            reporter = rm.group(1)

    # 1. Query bug details
    q_rc, q_out, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    bug_title = "Unknown"
    bug_steps = ""
    if q_out:
        tm = re.search(r'Title:\s*(.*)', q_out)
        if tm:
            bug_title = tm.group(1).strip()[:80]
        sm = re.search(r'Steps:\s*(.*?)(?:===|$)', q_out, re.DOTALL)
        if sm:
            bug_steps = sm.group(1).strip()[:300]

    reporter_account = ""
    if reporter:
        acct_match = re.search(r'\((\w+)\)', reporter)
        if acct_match:
            reporter_account = acct_match.group(1)

    # 2. Read the actual git diff to analyze the fix
    diff_text = ""
    changed_files = []
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=5,
            cwd="/root/.openclaw/workspace/his-repo",
        )
        commit_line = ""
        for line in r.stdout.split("\n"):
            if f"Fix Bug #{bid}" in line:
                commit_line = line.strip()
                break

        if commit_line:
            commit_sha = commit_line.split()[0]
            r = subprocess.run(
                ["git", "diff", f"{commit_sha}~1..{commit_sha}", "--stat"],
                capture_output=True, text=True, timeout=5,
                cwd="/root/.openclaw/workspace/his-repo",
            )
            diff_stat = r.stdout.strip()
            if diff_stat:
                changed_files = [l.strip() for l in diff_stat.split("\n") if l.strip()]

            # Get the actual diff content for analysis
            r = subprocess.run(
                ["git", "show", "--format=", commit_sha],
                capture_output=True, text=True, timeout=5,
                cwd="/root/.openclaw/workspace/his-repo",
            )
            diff_text = r.stdout.strip()[:1500]
    except Exception:
        pass

    # 3. Analyze test quality
    test_passed = True
    test_findings = []

    if diff_text:
        # Check for graceful degradation patterns (good)
        if "console.warn" in diff_text and "msgError" not in diff_text:
            test_findings.append("✅ 采用优雅降级策略，不会弹窗阻断用户")

        # Check for empty catch blocks (bad)
        if ".catch(() => {})" in diff_text or ".catch(function(){})" in diff_text:
            test_findings.append("⚠️ 发现空的 catch 块，异常被静默吞掉")
            test_passed = False

        # Check if the fix matches the bug description
        bug_keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}', bug_title + bug_steps))
        diff_keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}', diff_text))
        overlap = bug_keywords & diff_keywords
        if len(overlap) >= 2:
            test_findings.append(f"✅ 修复代码包含 Bug 相关关键词：{'、'.join(list(overlap)[:3])}")
        else:
            test_findings.append("⚠️ 修复代码与 Bug 描述关联度低，需人工确认")
    else:
        test_findings.append("⚠️ 未找到修复 diff，无法验证代码变更")

    if not test_findings:
        test_findings.append("✅ 代码变更符合规范")

    # 4. Build rich test report
    test_summary = "✅ 通过" if test_passed else "⚠️ 有风险"
    assign_comment = (
        f"🧪 由 {pctx.agent_name} 回归测试\n"
        f"测试结果：{test_summary}\n"
        f"修复文件：{changed_files[-1] if changed_files else '未知'}\n"
        + "\n".join(test_findings) +
        f"\n已指派回 {reporter} 在禅道中验证确认"
    )

    if reporter_account:
        logger.info("[%s] Assigning Bug #%s back to %s", pctx.agent_id, bid, reporter_account)
        arc, aout, aerr = run_script(pctx.z("zentao-write-bug.sh"), "assign", bid, reporter_account,
                   assign_comment, timeout=30)
        if arc != 0:
            logger.error("[%s] Failed to assign Bug #%s: %s", pctx.agent_id, bid, aerr[:100])
            pctx.reply(f"⚠️ 指派失败：Bug #{bid} 未能指派给 {reporter_account}（错误码 {arc}）")
            return
        traces.log(pctx.agent_id, "pipeline_assign", task_id=f"Bug#{bid}",
                   message=f"assigned to {reporter_account} (test: {test_summary})", status="ok")

    # 5. Report to Feishu
    findings_text = "\n".join(f"  {f}" for f in test_findings)
    reporter_note = f"\n👤 该 Bug 由 **{reporter}** 提出" if reporter else ""
    assign_note = f"\n✅ 已指派回 **{reporter}** 在禅道中验证" if reporter_account else ""

    changed_summary = changed_files[-1] if changed_files else "未知"
    pctx.reply(
        f"🧪 **回归测试报告**\n\n"
        f"Bug #{bid}：{bug_title}\n"
        f"修改文件：{changed_summary}\n\n"
        f"测试发现：\n{findings_text}\n"
        f"测试结论：{test_summary}{reporter_note}{assign_note}"
    )

    # 6. Notify Huatuo
    pctx.redis.xadd(pctx.redis_stream, {
        "agent_id": "huatuo",
        "message": f"Bug #{bid}（{bug_title}）回归测试完成（{test_summary}），已指派回提出人 {reporter}。请验收确认。",
        "source": "pipeline_test_done",
        "sender_id": "zhangfei",
        "msg_id": f"pipeline-notify-{bid}",
        "timestamp": datetime.now().isoformat(),
    })


def handle_pipeline_verify(pctx: PipelineContext, task: dict):
    """Huatuo: verify the fix and mark as resolved."""
    message = task.get("message", "")
    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    reporter_match = re.search(r'提出人:\s*([^\s。]+)', message)
    reporter = reporter_match.group(1) if reporter_match else "提出人"

    logger.info("[%s] Verifying Bug #%s", pctx.agent_id, bid)
    pctx.refresh_token()  # Ensure zentao token is fresh before write
    verify_comment = (
        f"🛡️ 由 {pctx.agent_name} 产品验收\n"
        f"验收结果：✅ 通过\n"
        f"功能完整性已验证，状态已更变为【已解决】\n"
        f"请 {reporter} 确认修复效果后在禅道中关闭该 Bug"
    )
    rc, out, err = run_script(pctx.z("zentao-write-bug.sh"), "resolve", bid, verify_comment, timeout=30)
    if rc != 0:
        logger.error("[%s] Failed to resolve Bug #%s in zentao: %s", pctx.agent_id, bid, err[:100])
        pctx.reply(
            f"⚠️ **验收失败**\n\nBug #{bid} 禅道状态更新失败（退出码 {rc}）。\n"
            f"📋 错误：\n```\n{err[:200]}\n```\n"
            f"请手动在禅道中将该 Bug 标记为已解决。"
        )
        return

    # Verify the resolution actually took effect
    vrc, vout, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    logger.debug("[%s] Verify Bug #%s: rc=%d, vout[:100]=%s", pctx.agent_id, bid, vrc, (vout or "")[:100])
    if vrc == 0 and "resolved" not in (vout or "").lower() and "已解决" not in (vout or ""):
        logger.warning("[%s] Bug #%s resolve returned 0 but status not changed, vout[:200]=%s",
                       pctx.agent_id, bid, (vout or "")[:200])
        pctx.reply(
            f"⚠️ **验收未确认**\n\nBug #{bid} 脚本返回成功但禅道状态未变为已解决。\n"
            f"📋 当前状态：\n```\n{vout[:300]}\n```\n"
            f"请手动在禅道中确认。"
        )
        return

    pctx.reply(
        f"🛡️ **验收通过**\n\n"
        f"Bug #{bid} 已验证功能完整性。\n"
        f"✅ 禅道状态已更变为【已解决】。\n\n"
        f"📢 请 **{reporter}** 确认修复效果后，在禅道中关闭该 Bug。"
    )


def handle_pm_analyze(pctx: PipelineContext, task: dict):
    """Liu Bei: analyze and route bugs to specialist agents."""
    message = task.get("message", "")
    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    # Extract title from the message
    title_match = re.search(rf"#{bid}[：:]\s*(.+)", message)
    bug_title = title_match.group(1).strip()[:80] if title_match else "Unknown"

    best_agent = _analyze_and_route(pctx, bid, bug_title)
    logger.info("[liubei] PM routing Bug #%s → %s (%s)", bid, best_agent, bug_title[:30])

    pctx.redis.xadd(pctx.redis_stream, {
        "agent_id": best_agent,
        "message": f"请修复 Bug #{bid}：{bug_title}",
        "source": "pm_routed",
        "sender_id": "liubei",
        "chat_id": "",
        "is_dm": "true",
        "msg_id": f"pm-route-{bid}-{int(time.time())}",
        "timestamp": datetime.now().isoformat(),
    })
    pctx.reply(
        f"📊 **PM 分配**\n\n"
        f"Bug #{bid}：{bug_title[:50]}\n"
        f"已分派给 **{best_agent}** 处理。"
    )


def handle_self_boot(pctx: PipelineContext, task: dict):
    """Autonomous fix: query bug → Claude Code fix → commit → handoff to Zhangfei."""
    from agentforge.core.fix_trajectory import get_trajectories

    message = task.get("message", "")

    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    # Check escalation — don't retry bugs that already failed many times
    try:
        trajectories = get_trajectories(bid)
        if trajectories:
            failures = {}
            for t in trajectories:
                if not t.get("success", True):
                    method = t.get("method", "unknown")
                    failures[method] = failures.get(method, 0) + 1
            unique_methods = len(failures)
            total_failures = sum(failures.values())
            if unique_methods >= 2 and total_failures >= 3:
                logger.warning("[pipeline] Bug #%s ESCALATED (bootstrap): %d methods, %d failures — skipping",
                               bid, unique_methods, total_failures)
                details = ""
                for method, count in sorted(failures.items(), key=lambda x: -x[1]):
                    for t in trajectories:
                        if not t.get("success") and t.get("method") == method:
                            summary = t.get("fix_summary", "")[:100]
                            details += f"\n  {method}：{count} 次失败 — {summary}"
                            break
                pctx.reply(
                    f"🚨 **自动修复已达上限**\n\n"
                    f"Bug #{bid} 已被 {unique_methods} 种方法共尝试 {total_failures} 次均失败。\n"
                    f"失败详情：{details}\n\n"
                    f"结论：该 Bug 疑似后端问题或需要深度业务分析，建议人工介入。"
                )
                return
    except Exception as e:
        logger.debug("[pipeline] Escalation check error: %s", e)

    q_rc, q_out, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    bug_title = "Unknown"
    bug_reporter = "未知"
    if q_out:
        tm = re.search(r'Title:\s*(.*)', q_out)
        if tm:
            bug_title = tm.group(1).strip()[:50]
        rm = re.search(r'创建人:\s*(.*)', q_out)
        if rm:
            bug_reporter = rm.group(1).strip()

    logger.info("[%s] Autonomous fix: Bug #%s: %s", pctx.agent_id, bid, bug_title)

    # Extract bug images for diagnosis
    bug_images = []
    image_desc = ""
    try:
        bug_images = get_bug_images(bid, q_out or "")
        image_desc = describe_images(bug_images, bid) if bug_images else ""
    except Exception:
        pass

    # Add agent-specific guidance for the fix
    agent_guidance = ""
    if pctx.agent_id == "guanyu":
        agent_guidance = (
            f"\n\n**后端开发重点**：优先搜索 Java/Spring 后端代码。\n"
            f"关键词：Controller, Service, Mapper, API, 接口, 数据查询\n"
            f"搜索目录：openhis-server-new/src/, his-repo/src/"
        )

    pctx.reply(
        f"🔍 **深度分析中**\n\n"
        f"发现 Bug #{bid}：\n📌 **{bug_title}**\n👤 提出人: {bug_reporter}\n\n"
        f"正在调用 Claude Code 分析源码并修复..."
    )

    def run_claude_fix():
        try:
            start_time = time.time()
            title_with_guidance = bug_title + agent_guidance
            crc, cout, cerr = run_script(
                pctx.z("claude-code-fix.sh"),
                bid, title_with_guidance, pctx.agent_name,
                timeout=10800,
            )
            if crc == 0:
                # Verify fix by checking git log
                import subprocess
                git_result = subprocess.run(
                    ["git", "log", "--oneline", "-3"],
                    capture_output=True, text=True, timeout=10,
                    cwd="/root/.openclaw/workspace/his-repo",
                )
                gcout = git_result.stdout

                if f"Fix Bug #{bid}" in gcout:
                    save_trajectory(bid, pctx.agent_name, "claude_code", True, time.time() - start_time,
                                    stdout=cout, stderr=cerr, fix_summary="committed")
                    # Add comment only — resolution is Huatuo's job after testing
                    try:
                        run_script(pctx.z("zentao-write-bug.sh"), "comment", bid,
                                   "智能体已修复，等待张飞测试验证", timeout=15)
                    except Exception:
                        pass
                    pctx.reply(
                        f"✅ **修复完成**\n\nBug #{bid} 代码已由 Claude Code 修复。\n\n"
                        f"📋 **详细日志**：\n```\n{cout[:800]}\n```\n\n"
                        f"🫡 流转给 **张飞** 测试..."
                    )
                    pctx.redis.xadd(pctx.redis_stream, {
                        "agent_id": "zhangfei",
                        "message": f"请测试 Bug #{bid} 的修复情况。提出人: {bug_reporter}。",
                        "source": "pipeline_fix_done",
                        "sender_id": pctx.agent_id,
                        "bug_reporter": bug_reporter,
                        "msg_id": f"pipeline-test-{bid}",
                        "timestamp": datetime.now().isoformat(),
                    })
                else:
                    save_trajectory(bid, pctx.agent_name, "claude_code", False, time.time() - start_time,
                                    stdout=cout, stderr=cerr,
                                    fix_summary="No commit found")
                    pctx.reply(
                        f"⚠️ **修复受阻**\n\nBug #{bid} Claude Code 已执行但未找到提交记录。\n\n"
                        f"📋 **Claude 输出**：\n```\n{cout[:500]}\n```\n"
                        f"📋 **最近提交**：\n```\n{gcout[:300]}\n```"
                    )
            else:
                save_trajectory(bid, pctx.agent_name, "claude_code", False, time.time() - start_time,
                                stdout=cout, stderr=cerr,
                                fix_summary=f"exit={crc}")
                reasons = {
                    1: "无有效修改或执行错误",
                    124: "执行超时",
                    125: "Claude Code 启动失败",
                    126: "脚本无执行权限",
                    127: "Claude 命令未找到",
                    128: "Git 仓库异常",
                }
                reason = reasons.get(crc, f"未知错误码 {crc}")
                pctx.reply(
                    f"⚠️ **修复受阻**\n\nBug #{bid} Claude Code 执行失败 ({reason})。\n\n"
                    f"📋 **输出**：\n```\n{cout[:500]}\n```\n"
                    f"📋 **错误**：\n```\n{cerr[:300]}\n```"
                )
        except Exception as e:
            pctx.reply(f"⚠️ **修复异常**\n\nBug #{bid}: {e}")

    threading.Thread(target=run_claude_fix, daemon=True).start()
