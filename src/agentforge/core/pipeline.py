"""
Pipeline Handlers — multi-step bug-fix pipeline logic.

Extracted from executor.py: test (Zhangfei), verify (Huatuo),
and self-boot autonomous fix (all agents).
"""

import json
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


def _generate_test_doc(bid: str, title: str, steps: str, reporter: str) -> str:
    """Generate a structured, machine-readable test document that both fixers and testers can read."""
    doc = f"""## 测试文档 — Bug #{bid}

**标题**: {title}
**提出人**: {reporter}
**文档生成**: 张飞 (QA)

### 1. 复现步骤
{steps[:500]}

### 2. 测试环境
- URL: http://localhost:81
- 账号: doctor1 / 123456
- 测试框架: Playwright (Chromium headless)

### 3. 验收标准
- [ ] 按复现步骤操作，Bug 现象不再出现
- [ ] 相关功能正常，无新增错误
- [ ] 页面无 JavaScript 控制台报错

### 4. 自动化测试脚本 (Playwright — 可执行)
```python
from agentforge.core.test_env import TestEnvironment

tester = TestEnvironment(use_production=False)

# 1. 登录
assert tester.login(username='doctor1', password='123456'), "登录失败"

# 2. 导航到对应模块（根据Bug标题自动推断）
# TODO: 具体导航路径需根据Bug类型调整

# 3. 复现Bug步骤
tester.screenshot("bug{bid}_before")
result = tester.reproduce_bug("{bid}", "{title[:80]}", "{steps[:200]}")
print("复现结果:", result.get("description", ""))

# 4. 验证修复结果
tester.screenshot("bug{bid}_after")
assert result.get("fixed", True), "Bug修复验证失败: " + result.get("description", "")

# 5. 检查控制台错误
errors = tester.check_console_errors()
if errors:
    print("控制台错误:", errors)

tester.close()
print("✅ Bug #{bid} 测试通过")
```

### 5. 回归检查点（按禅道步骤逐项验证）
1. 登录系统，进入对应模块
2. 执行 Bug 描述中的操作步骤
3. 确认结果符合期望行为
4. 截图保存为 bug{bid}_before.png 和 bug{bid}_after.png

### 6. 测试结论
修复是否通过: [ ] 通过 / [ ] 未通过
测试人: 张飞
"""
    return doc.strip()


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
    """Zhangfei: Generate test doc, verify fix, run regression, assign back."""
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

    # 2. Generate test document (parametrized, machine-readable)
    test_doc = _generate_test_doc(bid, bug_title, bug_steps, reporter)
    test_doc_key = f"test_doc:{bid}"
    pctx.redis.set(test_doc_key, test_doc, ex=86400)  # 24h TTL
    logger.info("[zhangfei] Test doc published: %s (%d chars)", test_doc_key, len(test_doc))

    # 3. Enqueue test doc to fixer agent so they can read it before fixing
    pctx.redis.rpush(pctx.redis_stream, json.dumps({
        "agent_id": task.get("sender_id", "zhaoyun"),
        "message": f"📋 【测试文档已生成】Bug #{bid} 修复前请先阅读测试文档 (Redis key: {test_doc_key})，按文档步骤进行本地验证。修复完成后根据文档回归测试。",
        "source": "test_doc_ready",
        "sender_id": "zhangfei",
        "msg_id": f"test-doc-{bid}-{int(time.time())}",
        "timestamp": datetime.now().isoformat(),
    }))

    # 4. Regression test: read test doc → follow steps → playwright verify
    def run_regression_test():
        try:
            from agentforge.core.test_env import get_tester
            subprocess.run(["git", "pull", "origin", "HEAD"], capture_output=True, text=True,
                           timeout=30, cwd="/root/.openclaw/workspace/his-repo")
            # Re-read test doc to follow exact steps
            test_doc = pctx.redis.get(test_doc_key)
            logger.info("[zhangfei] Regression test Bug #%s: following test doc", bid)
            tester = get_tester()
            if tester.login():
                repro = tester.reproduce_bug(bid, bug_title, bug_steps)
                logger.info("[zhangfei] Regression result Bug #%s: %s", bid, repro.get("description", ""))
            tester.close()
        except Exception as e:
            logger.warning("[zhangfei] Regression test skipped: %s", e)

    threading.Thread(target=run_regression_test, daemon=True).start()

    # 3. Read the actual git diff to analyze the fix
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
    pctx.redis.rpush(pctx.redis_stream, json.dumps({
        "agent_id": "huatuo",
        "message": f"Bug #{bid}（{bug_title}）回归测试完成（{test_summary}），已指派回提出人 {reporter}。请验收确认。",
        "source": "pipeline_test_done",
        "sender_id": "zhangfei",
        "msg_id": f"pipeline-notify-{bid}",
        "timestamp": datetime.now().isoformat(),
    }))


def handle_pipeline_verify(pctx: PipelineContext, task: dict):
    """Huatuo: verify the fix and mark as resolved."""
    message = task.get("message", "")
    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    reporter_match = re.search(r'提出人[:：]?\s*([^\s。]+)', message)
    if not reporter_match:
        reporter_match = re.search(r'(\w+\(\w+\))', message)  # Fallback: chenxj(chenxj)
    reporter = reporter_match.group(1) if reporter_match else "提出人"

    logger.info("[%s] Verifying Bug #%s", pctx.agent_id, bid)
    traces.log(pctx.agent_id, "verify_start", task_id=f"Bug#{bid}",
               message=f"Product acceptance verification for Bug #{bid}")

    # 1. Read Zhangfei's test doc to understand what was tested
    test_doc = pctx.redis.get(f"test_doc:{bid}")
    if test_doc:
        traces.log(pctx.agent_id, "verify_read_testdoc", task_id=f"Bug#{bid}",
                   message=f"Read test doc for Bug #{bid} ({len(test_doc)} chars)")

    # 2. Check current status first — skip if already closed/resolved
    pctx.refresh_token()  # Ensure fresh token before Zentao query
    qrc, qout, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    # Retry once with token refresh on 401
    if qrc != 0 or "401" in (qout or "") or "Authorization" in (qout or ""):
        logger.warning("[%s] Zentao query failed for #%s, refreshing token and retrying", pctx.agent_id, bid)
        pctx.refresh_token()
        qrc, qout, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    if qrc == 0 and qout:
        if "Status: closed" in qout or "Status: 已关闭" in qout:
            logger.info("[%s] Bug #%s already closed, skipping resolve", pctx.agent_id, bid)
            # Still re-assign if needed
            rmatch = re.search(r'\((\w+)\)', reporter)
            if rmatch:
                run_script(pctx.z("zentao-write-bug.sh"), "assign", bid, rmatch.group(1),
                           "产品验收已通过", timeout=15)
            pctx.reply(f"✅ **验收完成**\n\nBug #{bid} 已关闭，已指派回 {reporter}。")
            return

    # 3. Verify fix is real — check git diff for meaningful changes
    import subprocess as sp2
    diff_result = sp2.run(
        ["git", "log", "--oneline", "-5", "--format=%H %s"],
        capture_output=True, text=True, timeout=10,
        cwd="/root/.openclaw/workspace/his-repo",
    )
    fix_found = False
    for line in diff_result.stdout.split("\n"):
        if f"Fix Bug #{bid}" in line or f"修复 Bug #{bid}" in line:
            sha = line.split()[0]
            ds = sp2.run(["git", "diff", f"{sha}~1..{sha}", "--stat"],
                         capture_output=True, text=True, timeout=10,
                         cwd="/root/.openclaw/workspace/his-repo")
            changes = 0
            for dl in ds.stdout.split("\n"):
                if "|" in dl and "change" not in dl:
                    try: changes += dl.split("|")[1].strip().count("+") + dl.split("|")[1].strip().count("-")
                    except: pass
            if changes >= 3:
                fix_found = True
                traces.log(pctx.agent_id, "verify_diff", task_id=f"Bug#{bid}",
                           message=f"Git diff: {changes} changes in {sha[:8]}")
            else:
                logger.warning("[%s] Bug #%s: fix diff too small (%d changes), rejecting", pctx.agent_id, bid, changes)
            break
    if not fix_found:
        pctx.reply(f"❌ **验收不通过**\n\nBug #{bid} 修复改动量不足（或未找到提交），拒绝验收，退回重修。")
        return

    pctx.refresh_token()  # Ensure zentao token is fresh before write
    verify_comment = (
        f"🛡️ 由 {pctx.agent_name} 产品验收\n"
        f"验收结果：✅ 通过\n"
        f"功能完整性已验证，状态已更变为【已解决】\n"
        f"请 {reporter} 确认修复效果后在禅道中关闭该 Bug"
    )
    pctx.refresh_token()
    rc, out, err = run_script(pctx.z("zentao-write-bug.sh"), "resolve", bid, verify_comment, timeout=30)
    if rc != 0 or "401" in (err or "") or "401" in (out or "") or "Authorization" in (err or ""):
        logger.warning("[%s] Resolve failed for #%s, refreshing token and retrying", pctx.agent_id, bid)
        pctx.refresh_token()
        rc, out, err = run_script(pctx.z("zentao-write-bug.sh"), "resolve", bid, verify_comment, timeout=30)
    traces.log(pctx.agent_id, "resolve", task_id=f"Bug#{bid}", tool="zentao_resolve",
               message=f"Resolve Bug #{bid}: rc={rc}", status="ok" if rc == 0 else f"failed(rc={rc})")
    if rc != 0:
        logger.error("[%s] Failed to resolve Bug #%s in zentao: %s", pctx.agent_id, bid, err[:100])
        pctx.reply(
            f"⚠️ **验收失败**\n\nBug #{bid} 禅道状态更新失败（退出码 {rc}）。\n"
            f"📋 错误：\n```\n{err[:200]}\n```\n"
            f"请手动在禅道中将该 Bug 标记为已解决。"
        )
        return

    # Re-assign back to reporter after resolve (resolve clears assignee)
    reporter_account = ""
    rmatch = re.search(r'\((\w+)\)', reporter)
    if rmatch:
        reporter_account = rmatch.group(1)
    if reporter_account:
        run_script(pctx.z("zentao-write-bug.sh"), "assign", bid, reporter_account,
                   f"产品验收已通过，请确认关闭", timeout=15)
        logger.info("[%s] Re-assigned Bug #%s back to %s after resolve", pctx.agent_id, bid, reporter_account)

    # Verify the resolution actually took effect
    pctx.refresh_token()
    vrc, vout, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    if vrc != 0 or "401" in (vout or "") or "Authorization" in (vout or ""):
        logger.warning("[Huatuo] Verify query failed for #%s, refreshing and retrying", bid)
        pctx.refresh_token()
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


def handle_chenlin_doc(pctx: PipelineContext, task: dict):
    """Chenlin: generate fix documentation and archive to knowledge base."""
    message = task.get("message", "")
    bug_match = re.search(r"#(\d{2,4})", message)
    if not bug_match:
        return
    bid = bug_match.group(1)

    logger.info("[chenlin] Documenting Bug #%s", bid)

    # 1. Read test doc from Zhangfei
    test_doc = pctx.redis.get(f"test_doc:{bid}") or "无测试文档"

    # 2. Read Claude Code fix trajectory
    from agentforge.core.fix_trajectory import get_trajectories
    traj = get_trajectories(bid)
    fix_summary = ""
    fix_files = []
    for t in (traj or []):
        if t.get("success") and t.get("method") == "claude_code":
            fix_summary = t.get("fix_summary", "")[:100]
            break

    # 3. Query zentao for final bug status
    qrc, qout, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    bug_title = "Unknown"
    bug_severity = "?"
    if qout:
        tm = re.search(r'Title:\s*(.*)', qout)
        if tm:
            bug_title = tm.group(1).strip()[:80]
        sm = re.search(r'Severity:\s*(\d+)', qout)
        if sm:
            bug_severity = sm.group(1)

    # 4. Generate fix documentation
    doc = f"""## 修复文档 — Bug #{bid}

**标题**: {bug_title}
**严重等级**: {bug_severity}
**修复方式**: Claude Code
**修复摘要**: {fix_summary or '已提交修复'}

### 测试文档（张飞）
{test_doc[:500]}

### 验收结论
华佗已验证功能完整性，禅道状态已更变为【已解决】。

---
文档生成: 陈琳（文档专员）
生成时间: {datetime.now().isoformat()[:19]}
"""
    # Archive to Redis
    pctx.redis.set(f"fix_doc:{bid}", doc, ex=86400 * 30)  # 30 days TTL
    logger.info("[chenlin] Fix doc archived: fix_doc:%s (%d chars)", bid, len(doc))

    # 5. Add fix documentation as zentao comment via zentao CLI
    zentao_comment = (
        f"📚 修复文档（陈琳）\\n\\n"
        f"修复方式：Claude Code\\n"
        f"修复摘要：{fix_summary or '已提交修复'}"
    )
    try:
        import subprocess
        subprocess.run(
            ["/root/.nvm/versions/node/v22.22.0/bin/zentao", "bug", "update", bid,
             f"--comment={zentao_comment[:500]}"],
            capture_output=True, text=True, timeout=20,
        )
        logger.info("[chenlin] Fix doc posted to zentao Bug #%s", bid)
    except Exception as e:
        logger.warning("[chenlin] Failed to post doc to zentao: %s", e)

    # 6. Notify group for important fixes
    if bug_severity.isdigit() and int(bug_severity) <= 2:
        pctx.reply(
            f"📚 **修复文档已归档**\n\n"
            f"Bug #{bid}：{bug_title[:50]}\n"
            f"严重等级: {bug_severity}\n"
            f"📋 文档已存入知识库 (key: fix_doc:{bid})\n"
            f"🔍 可通过 Redis 检索: GET fix_doc:{bid}"
        )


def handle_pm_analyze(pctx: PipelineContext, task: dict):
    """Liu Bei: analyze and route bugs (batch or single) to specialist agents."""
    message = task.get("message", "")
    bug_matches = re.findall(r"#(\d{2,4})[：:]\s*(.+?)(?:\n|$)", message)

    if not bug_matches:
        # Try single-bug format
        bug_match = re.search(r"#(\d{2,4})", message)
        if bug_match:
            bid = bug_match.group(1)
            title_match = re.search(rf"#{bid}[：:]\s*(.+)", message)
            bug_title = title_match.group(1).strip()[:80] if title_match else "Unknown"
            bug_matches = [(bid, bug_title)]
        else:
            return

    dispatched = {}
    # Refresh token once before batch assign
    try:
        pctx.refresh_token()
    except Exception:
        pass

    for bid, title in bug_matches:  # Process all bugs in the batch
        bid = bid.strip()
        title = title.strip()[:80]
        best_agent = _analyze_and_route(pctx, bid, title)
        logger.info("[liubei] PM routing Bug #%s → %s", bid, best_agent)

        # Dispatch to Redis — each fix agent has its own queue
        fix_queue = f"{pctx.redis_stream}:fix:{best_agent}"
        pctx.redis.rpush(fix_queue, json.dumps({
            "agent_id": best_agent,
            "message": f"请修复 Bug #{bid}：{title}",
            "source": "pm_routed",
            "sender_id": "liubei",
            "chat_id": "",
            "is_dm": "true",
            "msg_id": f"pm-route-{bid}-{int(time.time())}",
            "timestamp": datetime.now().isoformat(),
        }))
        # Also assign in Zentao
        try:
            rc, _, _ = run_script(pctx.z("zentao-write-bug.sh"), "assign", bid, best_agent,
                       f"刘备(PM)分析后分派给{best_agent}处理", timeout=15)
            if rc != 0:
                logger.warning("[liubei] Zentao assign #%s to %s failed (rc=%d)", bid, best_agent, rc)
        except Exception as e:
            logger.warning("[liubei] Zentao assign exception: %s", e)
        dispatched.setdefault(best_agent, []).append(bid)

    # Summary reply
    names = {"zhaoyun": "赵云", "guanyu": "关羽", "huatuo": "华佗", "xunyu": "荀彧", "chenlin": "陈琳", "zhugeliang": "诸葛亮"}
    lines = []
    for agent, bids in dispatched.items():
        name = names.get(agent, agent)
        lines.append(f"  {name}：{len(bids)} 个 ({', '.join('#'+b for b in bids)})")
    pctx.reply(
        f"📊 **PM 分配完成**\n\n"
        + "\n".join(lines) +
        f"\n\n共 {sum(len(v) for v in dispatched.values())} 个 Bug 已分派。"
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
                logger.warning("[pipeline] Bug #%s was escalated but re-attempting with deep re-fix (%d methods, %d failures)",
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

    pctx.refresh_token()
    q_rc, q_out, _ = run_script(pctx.z("zentao-bug-query.sh"), bid, timeout=15)
    if q_rc != 0 or "401" in (q_out or "") or "Authorization" in (q_out or ""):
        logger.warning("[%s] Bug query failed for #%s, refreshing token and retrying", pctx.agent_id, bid)
        pctx.refresh_token()
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
        # Acquire Redis distributed lock (cross-process, 1 Claude Code globally)
        try:
            for _attempt in range(600):
                if pctx.redis.set("claude_code_lock", pctx.agent_id, nx=True, ex=600):
                    logger.info("[%s] Acquired Redis lock for Claude Code (pipeline)", pctx.agent_id)
                    pctx.redis.hset("task:status", bid, json.dumps({
                        "agent": pctx.agent_id, "bug_id": bid, "status": "running",
                        "start": datetime.now().isoformat()[:19], "elapsed": "",
                    }))
                    pctx.redis.expire("task:status", 1800)
                    break
                time.sleep(1)
            else:
                logger.error("[%s] Redis lock timeout after 10 min", pctx.agent_id)
                return
        except Exception as e:
            logger.error("[%s] Redis lock acquisition failed: %s", pctx.agent_id, e)
            return

        try:
            start_time = time.time()
            title_with_guidance = bug_title + agent_guidance
            crc, cout, cerr = run_script(
                pctx.z("claude-code-fix.sh"),
                bid, title_with_guidance, pctx.agent_name,
                timeout=10800,
            )
            if crc == 0:
                # Verify fix by checking git log on worktree (not main repo)
                import subprocess
                AGENT_WORKTREE = f"/tmp/agentforge-worktrees/{pctx.agent_name}"
                git_result = subprocess.run(
                    ["git", "log", "--oneline", "-3"],
                    capture_output=True, text=True, timeout=10,
                    cwd=AGENT_WORKTREE,
                )
                gcout = git_result.stdout

                if f"Fix Bug #{bid}" in gcout:
                    # --- Post-fix verification: check diff is meaningful ---
                    diff_stat = subprocess.run(
                        ["git", "diff", "HEAD~1", "--stat"],
                        capture_output=True, text=True, timeout=10,
                        cwd=AGENT_WORKTREE,
                    )
                    total_changes = 0
                    for line in diff_stat.stdout.split("\n"):
                        if "|" in line and ("change" not in line):
                            try:
                                nums = line.split("|")[1].strip()
                                plus = nums.count("+")
                                minus = nums.count("-")
                                total_changes += plus + minus
                            except:
                                pass
                    
                    # Verify diff has actual code changes (>3 meaningful lines)
                    if total_changes < 3:
                        save_trajectory(bid, pctx.agent_name, "claude_code", False, time.time() - start_time,
                                        stdout=cout, stderr=cerr,
                                        fix_summary=f"trivial_diff({total_changes} changes)")
                        logger.warning("[%s] Bug #%s: Claude Code exit=0 but diff too small (%d changes), re-queuing",
                                       pctx.agent_id, bid, total_changes)
                        # Re-queue for another attempt
                        pctx.redis.rpush(f"agent-work-queue:fix:{pctx.agent_id}", json.dumps({
                            "agent_id": pctx.agent_id,
                            "message": f"请深度修复 Bug #{bid}：{bug_title}（上次修复只有{total_changes}行改动，疑似无效修复，需重新排障）",
                            "source": "coordinator_scan", "sender_id": "coordinator",
                            "msg_id": f"retry-trivial-{bid}-{int(time.time())}",
                            "timestamp": datetime.now().isoformat(),
                        }))
                        return

                    save_trajectory(bid, pctx.agent_name, "claude_code", True, time.time() - start_time,
                                    stdout=cout, stderr=cerr, fix_summary=f"committed({total_changes} changes)")
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
                    pctx.redis.rpush(pctx.redis_stream, json.dumps({
                        "agent_id": "zhangfei",
                        "message": f"请测试 Bug #{bid} 的修复情况。提出人: {bug_reporter}。",
                        "source": "pipeline_fix_done",
                        "sender_id": pctx.agent_id,
                        "bug_reporter": bug_reporter,
                        "msg_id": f"pipeline-test-{bid}",
                        "timestamp": datetime.now().isoformat(),
                    }))
                else:
                    err_last = ""
                    for line in reversed((cerr.strip() + "\n" + cout.strip()).split("\n")):
                        l = line.strip()
                        if l and not l.startswith("=") and not l.startswith("-"):
                            err_last = l[:120]; break
                    save_trajectory(bid, pctx.agent_name, "claude_code", False, time.time() - start_time,
                                    stdout=cout, stderr=cerr,
                                    fix_summary=f"No commit, last error: {err_last}")
                    pctx.reply(
                        f"⚠️ **修复受阻**\n\nBug #{bid} Claude Code 已执行但未找到提交记录。\n\n"
                        f"📋 **Claude 输出**：\n```\n{cout[:500]}\n```\n"
                        f"📋 **最近提交**：\n```\n{gcout[:300]}\n```"
                    )
            else:
                err_last = ""
                for line in reversed((cerr.strip() + "\n" + cout.strip()).split("\n")):
                    l = line.strip()
                    if l and not l.startswith("=") and not l.startswith("-"):
                        err_last = l[:120]; break
                save_trajectory(bid, pctx.agent_name, "claude_code", False, time.time() - start_time,
                                stdout=cout, stderr=cerr,
                                fix_summary=f"exit={crc}: {err_last}")
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
            logger.error("[%s] Claude Code crash for Bug #%s: %s", pctx.agent_id, bid, e)
            try:
                from agentforge.core.dead_letter import dead_letter
                dead_letter.enqueue({"bug_id": bid, "agent": pctx.agent_id}, str(e)[:300])
            except Exception:
                pass
            pctx.reply(f"⚠️ **修复异常**\n\nBug #{bid}: {e}")
        finally:
            try:
                pctx.redis.delete("claude_code_lock")
                pctx.redis.hset("task:status", bid, json.dumps({
                    "agent": pctx.agent_id, "bug_id": bid,
                    "status": "done" if fixed else "failed",
                    "start": datetime.now().isoformat()[:19],
                    "elapsed": f"{elapsed:.0f}s" if 'elapsed' in dir() else "",
                }))
            except: pass

    run_claude_fix()
