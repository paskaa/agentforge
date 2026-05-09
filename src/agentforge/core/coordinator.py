"""
Coordinator — cross-agent bug orchestration (v2).

Periodically scans ALL agent bugs from Zentao.
For each bug:
  - Skip if assigned to a known human (not an agent account)
  - Route to the best agent by expertise keywords (not by current assignee)
  - Dispatch up to 3 bugs per agent for parallel fixing
  - Report summary to Feishu group
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from agentforge.core.tool_executor import run_script

logger = logging.getLogger("agentforge.coordinator")

# All 8 agent accounts — liubei is PM, gets summary not fix tasks
ALL_AGENTS = ["zhugeliang", "liubei", "guanyu", "zhaoyun", "xunyu", "zhangfei", "huatuo", "chenlin"]
FIXER_AGENTS = ["zhugeliang", "guanyu", "zhaoyun", "xunyu", "zhangfei", "huatuo", "chenlin"]

# Agent account mapping
AGENT_ACCOUNTS = {a: a for a in ALL_AGENTS}

# Known human accounts — bugs assigned to these are skipped
HUMAN_ACCOUNTS = {
    "chenxj", "sjjh", "admin", "doctor1", "ssshs1",
    "yangkexiang", "yangkeixang",  # 杨科祥
}

# Expertise routing keywords
EXPERTISE = {
    "zhugeliang": ["架构", "设计", "方案", "review", "重构", "规范", "api设计", "安全"],
    "liubei": ["汇总", "项目", "进度", "管理", "分配", "协调", "报告", "统计", "概览"],
    "guanyu": ["后端", "java", "api", "接口", "服务", "spring", "service", "controller", "mapper", "后端报错",
               # 前端修不好的bug往往是后端问题 — 从轨迹分析中自动识别
               "数据为空", "加载失败", "无权限", "接口报错", "500", "404", "超时",
               "无响应", "保存失败", "提交失败", "异常报错", "返回为空",
               "签发", "计费", "收费", "退费", "医嘱保存", "医嘱提交", "医嘱删除",
               "库存", "入出库", "领用", "退库", "盘存", "作废", "冲销"],
    "zhaoyun": ["前端", "vue", "页面", "样式", "css", "组件", "表单", "按钮", "ui", "弹窗", "对话框", "列表", "表格", "输入框", "下拉", "选择", "勾选", "回显", "渲染"],
    "xunyu": ["数据库", "sql", "表", "查询", "索引", "性能", "慢查询", "优化", "数据", "mysql"],
    "zhangfei": ["测试", "bug", "缺陷", "验证", "复现", "禅道"],
    "huatuo": ["产品", "需求", "功能", "用户", "体验", "prd", "业务流程", "临床", "his", "门诊", "住院", "医生站", "护士站"],
    "chenlin": ["文档", "说明", "手册", "wiki", "知识库", "培训", "发布", "公告"],
}


class Coordinator:
    """Scans all agent bugs and distributes to specialist agents by expertise."""

    def __init__(self, zentao_dir: Path, agent_accounts: dict,
                 redis, redis_stream: str, reply_fn):
        self.zentao_dir = zentao_dir
        self.agent_accounts = agent_accounts
        self.redis = redis
        self.redis_stream = redis_stream
        self.reply = reply_fn
        self._last_scan = 0

    def scan_and_dispatch(self, min_interval: int = 300) -> int:
        now = time.time()
        if now - self._last_scan < min_interval:
            return 0
        self._last_scan = now

        logger.info("[coordinator] Scanning all agent bugs...")

        # Refresh zentao token first
        rc, _, _ = run_script(
            self.zentao_dir / "zentao-token-refresh.sh",
            "zhangfei", timeout=10,
        )
        if rc != 0:
            logger.warning("[coordinator] Token refresh failed, scan may fail")

        # Collect all bugs across all agents
        all_bugs: list[tuple[str, str, str]] = []  # (bug_id, title, assigned_agent)

        for agent_id in ALL_AGENTS:
            account = self.agent_accounts.get(agent_id, agent_id)
            rc, out, _ = run_script(
                self.zentao_dir / "zentao-my-bugs.sh",
                account, "active", timeout=60,
            )
            if rc != 0 or not out:
                continue
            if "名下没有未解决的 Bug" in out or "当前所有任务已完成" in out:
                continue

            # Extract each bug's ID and title
            # Parse format: "#NNN: Title" or "#NNN  Title"
            bug_matches = re.findall(r"#(\d{2,4})\s*[:：]?\s*(.+?)(?:\n|$)", out)
            for bid, title in bug_matches:
                title = title.strip()[:80]
                all_bugs.append((bid, title, agent_id))

        if not all_bugs:
            logger.info("[coordinator] No agent bugs found.")
            return 0

        # Filter + Route: skip humans, route by expertise
        routed: dict[str, list[tuple[str, str]]] = {}  # target_agent -> [(bid, title)]
        skipped_human = 0
        human_fixed_bugs: list[tuple[str, str]] = []  # (bid, title) for pipeline

        for bid, title, assigned_agent in all_bugs:
            # Liu Bei's bugs: send to PM for analysis and distribution
            if assigned_agent == "liubei":
                # Skip if already routed (pm_routed exists for this bug)
                if self._already_routed(bid):
                    continue
                self.redis.xadd(self.redis_stream, {
                    "agent_id": "liubei",
                    "message": f"请分析和分派 Bug #{bid}：{title}",
                    "source": "pm_analyze",
                    "sender_id": "coordinator",
                    "chat_id": "",
                    "is_dm": "true",
                    "msg_id": f"coord-pm-analyze-{bid}-{int(time.time())}",
                    "timestamp": datetime.now().isoformat(),
                })
                total += 1
                continue

            # Human-assigned bugs: skip fix, but start pipeline if already fixed in git
            if self._is_human_assigned(assigned_agent):
                if self._is_fixed_in_git(bid):
                    human_fixed_bugs.append((bid, title))
                else:
                    skipped_human += 1
                continue

            # Skip bugs already resolved in zentao
            if self._is_resolved_in_zentao(bid):
                logger.info("[coordinator] Bug #%s already resolved in zentao, skipping", bid)
                continue

            # Skip escalated bugs
            if self._is_escalated(bid):
                logger.info("[coordinator] Bug #%s escalated, skipping distribution", bid)
                continue

            # Route to best agent by expertise
            best = self._route_bug(title, bid)
            # Dynamic re-route: frontend bugs that failed 2+ times → backend
            if best == "zhaoyun" and self._is_stuck_frontend(bid):
                logger.info("[coordinator] Bug #%s rerouted: zhaoyun → guanyu (stuck frontend)", bid)
                best = "guanyu"
            routed.setdefault(best, []).append((bid, title))

        # Agent display names
        names = {
            "zhugeliang": "诸葛亮", "liubei": "刘备", "guanyu": "关羽", "zhaoyun": "赵云",
            "xunyu": "荀彧", "zhangfei": "张飞", "huatuo": "华佗", "chenlin": "陈琳",
        }

        # Dispatch up to 3 per agent (exclude liubei from fix tasks)
        total = 0
        for target_agent, bugs in routed.items():
            if target_agent == "liubei":
                continue  # PM gets summary, not individual fix tasks
            for bid, title in bugs[:3]:
                self.redis.xadd(self.redis_stream, {
                    "agent_id": target_agent,
                    "message": f"请修复 Bug #{bid}：{title}",
                    "source": "coordinator_scan",
                    "sender_id": "coordinator",
                    "chat_id": "",
                    "is_dm": "true",
                    "msg_id": f"coord-{target_agent}-{bid}-{int(time.time())}",
                    "timestamp": datetime.now().isoformat(),
                })
                total += 1

        # Human-assigned bugs that were already fixed → zhangfei test pipeline
        for bid, title in human_fixed_bugs[:5]:
            self.redis.xadd(self.redis_stream, {
                "agent_id": "zhangfei",
                "message": f"请测试 Bug #{bid} 的修复情况。提出人: 陈显精(chenxj)。",
                "source": "pipeline_fix_done",
                "sender_id": "coordinator",
                "bug_reporter": "陈显精(chenxj)",
                "msg_id": f"coord-human-pipeline-{bid}",
                "timestamp": datetime.now().isoformat(),
            })
            total += 1
        if human_fixed_bugs:
            logger.info("[coordinator] Injected %d human-assigned bugs into test pipeline", len(human_fixed_bugs))

        # Send ONE summary to Liu Bei (PM) — full distribution report
        if total > 0:  # Only if we distributed bugs
            summary_lines = []
            for aid, bugs in routed.items():
                if aid == "liubei":
                    continue
                name = names.get(aid, aid)
                bug_ids = ", ".join(f"#{b[0]}" for b in bugs[:3])
                summary_lines.append(f"  {name}：{len(bugs)} 个 ({bug_ids})")
            full_summary = (
                f"协同扫描完成。共 {total} 个 Bug 已按专业分配：\n"
                + "\n".join(summary_lines) +
                f"\n\n我将跟进各负责人修复进度。"
            )
            self.redis.xadd(self.redis_stream, {
                "agent_id": "liubei",
                "message": full_summary,
                "source": "coordinator_scan",
                "sender_id": "coordinator",
                "chat_id": "",
                "is_dm": "true",
                "msg_id": f"coord-liubei-summary-{int(time.time())}",
                "timestamp": datetime.now().isoformat(),
            })

        # Report
        summary = []
        for aid, bugs in routed.items():
            name = names.get(aid, aid)
            tag = "🔧" if aid == "zhaoyun" else "🛠️" if aid == "guanyu" else "🤖"
            bug_ids_str = ', '.join('#'+b[0] for b in bugs[:2])
            summary.append("  " + tag + " " + name + "：" + str(len(bugs)) + " 个 (" + bug_ids_str + ")")

        msg = f"🤖 **协同扫描报告**\n\n发现 {len(all_bugs)} 个待修复 Bug\n"
        msg += f"已按专业领域智能路由到各 Agent：\n" + "\n".join(summary)
        if skipped_human > 0:
            msg += f"\n\n⏸️ 跳过 {skipped_human} 个分配给人类的 Bug（不处理）"
        msg += f"\n🚀 共分发 **{total}** 个修复任务，并行执行中..."
        self.reply(msg)

        logger.info("[coordinator] Dispatched %d bugs across %d agents (%d human skipped).",
                    total, len(routed), skipped_human)
        return total

    def _is_human_assigned(self, assigned_agent: str) -> bool:
        """Check if this bug is assigned to a human, not an agent."""
        return assigned_agent.lower() in HUMAN_ACCOUNTS

    def _route_bug(self, title: str, bug_id: str) -> str:
        """
        Route a bug to the best agent by expertise keyword match.
        Falls back to zhugeliang (架构师) for unclassifiable bugs.
        """
        best_agent = "zhugeliang"
        best_score = 0

        text = title.lower()
        for agent_id, keywords in EXPERTISE.items():
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > best_score:
                best_score = score
                best_agent = agent_id

        if best_score > 0:
            logger.debug("[coordinator] Bug #%s → %s (score=%d)", bug_id, best_agent, best_score)
        else:
            logger.debug("[coordinator] Bug #%s → %s (fallback)", bug_id, best_agent)

        return best_agent

    def _is_fixed_in_git(self, bug_id: str) -> bool:
        """Check if bug has a recent git commit (within 7 days)."""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "log", "--oneline", "--since=7 days ago", "--format=%s"],
                capture_output=True, text=True, timeout=5,
                cwd="/root/.openclaw/workspace/his-repo",
            )
            return f"Fix Bug #{bug_id}" in r.stdout or f"fix(#439,#{bug_id}" in r.stdout
        except Exception:
            return False

    def _already_routed(self, bug_id: str) -> bool:
        """Check if PM already routed this bug (pm_routed exists in stream)."""
        try:
            for mid, fields in self.redis.xrange(self.redis_stream, '-', '+', count=50):
                src = fields.get('source', '')
                if src == 'pm_routed' and str(bug_id) in fields.get('message', ''):
                    return True
        except Exception:
            pass
        return False

    def _is_resolved_in_zentao(self, bug_id: str) -> bool:
        """Quick check if bug is already resolved in zentao. Avoids re-fixing."""
        try:
            rc, out, _ = run_script(
                self.zentao_dir / "zentao-bug-query.sh", bug_id, timeout=10,
            )
            if rc == 0 and out:
                return "resolved" in out.lower() or "已解决" in out
        except Exception:
            pass
        return False

    def _is_escalated(self, bug_id: str) -> bool:
        """Check if bug has been escalated (2 methods, 3+ failures)."""
        try:
            from agentforge.core.fix_trajectory import get_trajectories
            trajectories = get_trajectories(bug_id)
            if not trajectories:
                return False
            failures = {}
            for t in trajectories:
                if not t.get("success", True):
                    method = t.get("method", "unknown")
                    failures[method] = failures.get(method, 0) + 1
            return len(failures) >= 2 and sum(failures.values()) >= 3
        except Exception:
            return False

    def _is_stuck_frontend(self, bug_id: str) -> bool:
        """
        Check if a bug has been attempted by frontend (zhaoyun) and failed 2+ times.
        This indicates the root cause is likely backend, not frontend code.
        """
        try:
            from agentforge.core.fix_trajectory import get_trajectories
            trajectories = get_trajectories(bug_id)
            if not trajectories:
                return False

            # Count zhaoyun's total attempted fixes (failures)
            zhaoyun_failures = sum(
                1 for t in trajectories
                if not t.get("success", True)
            )
            return zhaoyun_failures >= 2
        except Exception:
            return False
