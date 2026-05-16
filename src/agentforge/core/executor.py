"""
Agent Executor — Core agent execution engine.

Runs as a long-lived process: polls Redis Stream for tasks,
executes tools (via plugin registry), calls LLM, sends Feishu replies.

Architecture (v2):
  - Tool dispatch → ToolRegistry (core/tool_registry.py + builtin_tools.py)
  - Pipeline handlers → core/pipeline.py
  - LLM calls → core/llm.py (LLMClient with retry)
  - Feishu I/O → network/feishu.py (requests)
  - Config → config.py (dataclass, single source of truth)
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

import redis

from agentforge.config import Config
from agentforge.core.tool_registry import ToolRegistry, ToolContext, discover_tools
from agentforge.core.tool_executor import run_script
from agentforge.core.llm import LLMClient, SessionManager
from agentforge.core.pipeline import (
    PipelineContext,
    handle_pipeline_test,
    handle_pipeline_verify,
    handle_chenlin_doc,
    handle_self_boot,
    handle_pm_analyze,
)
from agentforge.core.subagent import SubAgentPool, SubAgentContext
from agentforge.core.coordinator import Coordinator
from agentforge.core.trace_store import traces
from agentforge.core.quota_monitor import mark_rate_limited, is_rate_limited
from agentforge.network.feishu import FeishuClient

logger = logging.getLogger("agentforge.executor")


class AgentExecutor:
    """Single agent instance — polls Redis, handles tasks, replies via Feishu."""

    def __init__(self, agent_id: str, config: Optional[Config] = None):
        self.agent_id = agent_id
        self.cfg = config or Config()
        self.agent_name = self.cfg.get_agent_name(agent_id)

        # --- Redis ---
        self.redis = redis.Redis(**self.cfg.redis_kwargs)

        # --- LLM ---
        llm_cfg = self.cfg.get_llm_config(agent_id)
        self.llm = LLMClient(
            api_key=llm_cfg["api_key"],
            api_base=llm_cfg["api_base"],
            model=llm_cfg["model"],
        )
        self.llm.model_routes = {
            "coding": self.cfg.model_routing.get("coding", "qwen-coder-plus"),
            "analysis": self.cfg.model_routing.get("analysis", "qwen-plus"),
            "simple": self.cfg.model_routing.get("simple", "qwen-turbo"),
            "default": llm_cfg["model"],
        }

        # --- Feishu ---
        self.feishu_app = self.cfg.get_feishu_app(agent_id)
        self.feishu = FeishuClient(
            app_id=self.feishu_app.get("appId", ""),
            app_secret=self.feishu_app.get("appSecret", ""),
            group_chat_id=self.cfg.feishu_group_chat_id,
        )
        self.group_chat_id = self.cfg.feishu_group_chat_id

        # --- Tool Registry ---
        self.tool_registry = ToolRegistry()
        import agentforge.core.builtin_tools as builtin
        discover_tools(self.tool_registry, builtin)
        logger.info("[%s] Loaded %d tool plugins", agent_id, len(self.tool_registry._tools))

        # --- Tool Context ---
        self.tool_ctx = ToolContext(
            agent_id=agent_id,
            agent_name=self.agent_name,
            zentao_dir=self.cfg.zentao_scripts_dir,
            scripts_dir=self.cfg.scripts_dir,
            agent_account=self.cfg.get_agent_account(agent_id),
            refresh_token=self._refresh_token,
        )

        # --- Pipeline Context ---
        self.pctx = PipelineContext(
            agent_id=agent_id,
            agent_name=self.agent_name,
            zentao_dir=self.cfg.zentao_scripts_dir,
            redis=self.redis,
            redis_stream=self.cfg.redis_stream,
            reply_fn=self.reply_feishu,
            refresh_fn=self._refresh_token,
        )

        # --- Sessions ---
        self.sessions = SessionManager(self.cfg.get_session_dir(agent_id))

        # --- Self-optimizer ---
        from agentforge.core.optimizer import SelfOptimizer
        self.optimizer = SelfOptimizer(agent_id=agent_id, config=self.cfg)

        # --- LLM Fixer (disabled — Claude Code handles all fixes) ---
        # from agentforge.core.llm_fixer import LLMFixer
        # self.llm_fixer = LLMFixer(self.llm)

        # --- SubAgent Pool (parallel bug fixing) ---
        max_subs = int(os.environ.get("SUBAGENT_MAX", "3").split("#")[0].strip())
        self.subpool = SubAgentPool(max_workers=max_subs)
        self.subctx = SubAgentContext(
            agent_id=agent_id,
            agent_name=self.agent_name,
            zentao_dir=self.cfg.zentao_scripts_dir,
            redis=self.redis,
            redis_stream=self.cfg.redis_stream,
            reply_fn=self.reply_feishu,
            refresh_fn=self._refresh_token,
            zentao_write_bug=self._zentao_write_bug,
            llm_fixer=None,  # LLM Fixer disabled
        )

        # --- Coordinator (only zhugeliang runs cross-agent scans) ---
        self.coordinator = None
        if agent_id in ("zhugeliang", "liubei"):
            self.coordinator = Coordinator(
                zentao_dir=self.cfg.zentao_scripts_dir,
                agent_accounts=self.cfg.agent_accounts,
                redis=self.redis,
                redis_stream=self.cfg.redis_stream,
                reply_fn=self.reply_feishu,
            )
            logger.info("[%s] Coordinator enabled", agent_id)

        # --- Hermes bridge (lazy) ---
        self._hermes = None

        logger.info("[%s] Started as %s (model: %s, subagents: %d)",
                    agent_id, self.agent_name, llm_cfg["model"], max_subs)

    # =========================================================================
    #  Helpers
    # =========================================================================

    def _refresh_token(self, force: bool = False):
        """Refresh zentao token with TTL caching (max once per 5 min)."""
        now = time.time()
        if not force and hasattr(self, '_token_refreshed_at') and now - self._token_refreshed_at < 300:
            return  # Token is fresh enough
        self._token_refreshed_at = now
        rc, out, err = run_script(
            self.cfg.zentao_scripts_dir / "zentao-token-refresh.sh",
            self.cfg.get_agent_account("zhangfei"),
            timeout=10,
        )
        if rc != 0:
            logger.warning("[%s] Token refresh failed (rc=%d): %s", self.agent_id, rc, err[:100])

    def _zentao_write_bug(self, action: str, bid: str, comment: str):
        """Write operation on zentao bug (resolve/assign)."""
        run_script(
            self.cfg.zentao_scripts_dir / "zentao-write-bug.sh",
            action, bid, comment, timeout=30,
        )

    def reply_feishu(self, text: str, target_id: str = "", id_type: str = "chat_id"):
        target = target_id or self.group_chat_id
        formatted = f"**{self.agent_name}** 回复：\n\n{text}"
        traces.log(self.agent_id, "feishu_reply", message=text[:200], status="sending")
        ok = self.feishu.send(formatted, target_id=target, id_type=id_type, agent_name=self.agent_name)
        if not ok:
            traces.log(self.agent_id, "feishu_reply", status="failed")
            logger.warning("[%s] Feishu send failed (target=%s)", self.agent_id, target[:20])
        else:
            traces.log(self.agent_id, "feishu_reply", message=text[:200], status="ok")

    # =========================================================================
    #  Tool execution — delegated to plugin registry
    # =========================================================================

    def execute_tools(self, message: str) -> tuple[Optional[str], Optional[str]]:
        """Execute matching tools via registry."""
        self._refresh_token()
        return self.tool_registry.execute(message, self.tool_ctx)

    # =========================================================================
    #  Intent routing
    # =========================================================================

    def should_respond(self, text: str) -> bool:
        # Direct mention always wins (highest priority)
        agent_name_en = self.cfg.get_agent_id_from_name(text) if hasattr(self.cfg, 'get_agent_id_from_name') else None
        if agent_name_en and agent_name_en == self.agent_id:
            return True

        # @all messages: best-matched agent answers (not everyone)
        if "@_user_1" in text or "@所有人" in text:
            text_lower = text.lower()
            my_keywords = self.cfg.expertise.get(self.agent_id, [])
            my_score = sum(1 for kw in my_keywords if kw in text_lower)
            other_max = 0
            for aid, kws in self.cfg.expertise.items():
                if aid == self.agent_id:
                    continue
                s = sum(1 for kw in kws if kw in text_lower)
                if s > other_max:
                    other_max = s
            # PM is fallback when no one matches
            if my_score > other_max or (my_score == other_max and self.agent_id == "liubei"):
                return True
            return False

        text_lower = text.lower()
        my_keywords = self.cfg.expertise.get(self.agent_id, [])
        my_score = sum(1 for kw in my_keywords if kw in text_lower)
        other_max = 0
        for aid, kws in self.cfg.expertise.items():
            if aid == self.agent_id:
                continue
            other_max = max(other_max, sum(1 for kw in kws if kw in text_lower))
        return my_score > 0 and my_score >= other_max and (my_score >= 2 or other_max == 0)

    # =========================================================================
    #  LLM calling
    # =========================================================================

    def call_llm_with_tools(self, user_message: str, conversation_id: str,
                            record_reflection: bool = False) -> str:
        if self.cfg.hermes_enabled and not is_rate_limited(self.agent_id):
            result = self._call_hermes(user_message, conversation_id)
            if result and "未返回结果" not in result:
                return result
            # Hermes returned empty (likely 429) → fall through to direct LLM
            mark_rate_limited(self.agent_id)

        system_prompt = self.optimizer.get_enhanced_system_prompt()
        history = self.sessions.load(conversation_id)

        raw_flag, tool_output = self.execute_tools(user_message)
        if raw_flag == "__RAW__":
            return tool_output

        if tool_output:
            system_prompt += f"\n\n【⚠️ 工具已执行，以下是真实数据，必须基于此回复】\n{tool_output}"
        else:
            system_prompt += "\n\n【💡 聊天模式】用户正在进行非工具性对话。请根据角色设定自然回复。"

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": user_message})

        model = self.llm.select_model("analysis" if tool_output else "simple")
        logger.info("[%s] Using model: %s", self.agent_id, model)
        traces.log(self.agent_id, "llm_call", model=model, task_id=conversation_id,
                   status="with_tools" if tool_output else "chat")

        _llm_start = time.time()
        reply = self.llm.call(messages, model=model)
        if reply:
            traces.log(self.agent_id, "llm_done", model=model,
                       duration_ms=int((time.time() - _llm_start) * 1000), status="ok")
        if reply is None:
            reply = "LLM 调用失败，请稍后重试"

        self.sessions.append(conversation_id, user_message, reply)

        if record_reflection and tool_output:
            threading.Thread(
                target=self.optimizer.reflect_on_task,
                args=(user_message, tool_output, reply, time.time()),
                daemon=True,
            ).start()

        return reply

    def _call_hermes(self, user_message: str, conversation_id: str) -> str:
        if self._hermes is None:
            from agentforge.hermes_bridge import HermesAgentWrapper
            self._hermes = HermesAgentWrapper(self.agent_id, hermes_home=str(self.cfg.hermes_home))
        history = self.sessions.load(conversation_id)
        reply = self._hermes.run(user_message, conversation_history=history[-6:])
        if reply:
            self.sessions.append(conversation_id, user_message, reply)
        return reply or "Hermes 未返回结果"

    # =========================================================================
    #  Task handler — main dispatch
    # =========================================================================

    def handle_task(self, task: dict):
        message = task.get("message", "")
        target = task.get("agent_id", "")
        source = task.get("source", "")
        msg_id = task.get("msg_id", "")
        redis_id = task.get("_redis_id", "")  # Redis stream ID for ACK

        # --- Routing ---
        # coordinator_scan / pm_routed: any fix agent can process
        # pipeline_* / self_boot_check: must route to specific agent
        pipeline_sources = ("pipeline_fix_done", "pipeline_test_done", "pm_analyze", 
                           "rerouted_to_backend", "self_boot_check")
        fix_agents = ("zhaoyun", "guanyu", "xunyu")
        
        if source in pipeline_sources and target != self.agent_id:
            # Pipeline message for wrong agent: re-push to queue
            self.redis.rpush(self.cfg.redis_stream, json.dumps(task))
            return
        
        if source == "coordinator_scan" and self.agent_id not in fix_agents:
            # Non-fix agent should not process coordinator_scan
            return
        
        if target == "broadcast":
            if source == "ws_listener" and not self.should_respond(message):
                return

        # PM: convert "@all 分配XX的Bug" into targeted distribution
        # Only trigger on explicit commands: "开始分配", "全部分配", "执行分配"
        if self.agent_id == "liubei" and source == "ws_listener" and message:
            trigger_words = ["开始分配", "全部分配", "执行分配", "立刻分配", "立即分配",
                           "马上分配", "把所有bug分配", "把所有Bug分配"]
            if not any(w in message for w in trigger_words):
                pass  # Don't trigger on casual mentions of "分配"
            elif "分配" in message or "分派" in message:
                import re as _re
                # Extract human name: "分配王怡哲的bug" → wangyizhe, "分配给陈显精" → chenxj
                name_map = {"王怡哲": "wangyizhe", "陈显精": "chenxj"}
                target_human = None
                for name, account in name_map.items():
                    if name in message:
                        target_human = account
                        break
                if target_human:
                    logger.info("[liubei] PM distributing bugs for: %s", target_human)
                    self.reply_feishu(f"📊 **收到分配请求**\n\n正在查询 {target_human} 名下的 Bug 并按专业领域分派给智能体...")
                    self.ack(redis_id)
                    self._handle_human_bug_distribution(target_human)
                    return

        logger.info("[%s] Processing: %s (model: %s)", self.agent_id, message[:60], self.llm.model)
        _task_start = time.time()

        # --- Pipeline dispatch (delegated to pipeline module) ---
        if source == "pm_analyze" and self.agent_id == "liubei":
            handle_pm_analyze(self.pctx, task)
            self.ack(redis_id)
            return
        if source == "pipeline_fix_done" and self.agent_id == "zhangfei":
            handle_pipeline_test(self.pctx, task)
            self.ack(redis_id)
            return
        if source == "pipeline_test_done" and self.agent_id == "huatuo":
            handle_pipeline_verify(self.pctx, task)
        if source == "pipeline_test_done" and self.agent_id == "chenlin":
            handle_chenlin_doc(self.pctx, task)
            self.ack(redis_id)
            return
        if source == "rerouted_to_backend" and self.agent_id == "guanyu":
            handle_self_boot(self.pctx, task)
            self.ack(redis_id)
            return
        if source == "pm_routed":
            traces.log(self.agent_id, "task_start", task_id=msg_id, message=message[:100], model=self.llm.model)
            handle_self_boot(self.pctx, task)
            time.sleep(10)  # Cooldown — wait for Claude Code to start
            return
        if source == "self_boot_check" or source == "coordinator_scan":
            traces.log(self.agent_id, "task_start", task_id=msg_id, message=message[:100], model=self.llm.model)
            handle_self_boot(self.pctx, task)
            time.sleep(10)  # Cooldown
            return

        # --- Normal: tool + LLM ---
        reply = self.call_llm_with_tools(message, msg_id, record_reflection=True)
        if not reply:
            self.ack(redis_id)
            return

        # After Hermes/LLM replies, check if message implies a pipeline action
        self._maybe_trigger_pipeline(message, reply, task, redis_id)

        chat_id = task.get("chat_id", "")
        sender_id = task.get("sender_id", "")
        is_dm = task.get("is_dm", "false") == "true"

        if is_dm and sender_id:
            self.feishu.send(
                f"**{self.agent_name}** 回复：\n\n{reply}",
                target_id=sender_id, id_type="open_id", agent_name=self.agent_name,
            )
            logger.info("[%s] Reply via DM to: %s", self.agent_id, sender_id)
        else:
            self.reply_feishu(reply)
            logger.info("[%s] Reply to GROUP", self.agent_id)

        self.ack(redis_id)
        traces.log(self.agent_id, "task_done", task_id=msg_id,
                   duration_ms=int((time.time() - _task_start) * 1000),
                   status="ok")

    def _handle_human_bug_distribution(self, human_account: str = ""):
        """Query human-assigned bugs and inject them into PM analysis pipeline."""
        import subprocess
        humans = [human_account] if human_account else ["wangyizhe", "chenxj"]
        self._refresh_token()
        for human in humans:
            r = subprocess.run(
                [str(self.cfg.zentao_scripts_dir / "zentao-my-bugs.sh"), human, "active"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0 or "401" in (r.stdout or "") or "Authorization" in (r.stdout or ""):
                logger.warning("[%s] Human bug query failed for %s, refreshing and retrying", self.agent_id, human)
                self._refresh_token()
                r = subprocess.run(
                    [str(self.cfg.zentao_scripts_dir / "zentao-my-bugs.sh"), human, "active"],
                    capture_output=True, text=True, timeout=60,
                )
            if r.returncode != 0 or not r.stdout:
                continue
            # Parse bug IDs and titles
            import re
            bugs = re.findall(r"#(\d{2,4})\s*[：:]\s*(.+?)(?:\n|$)", r.stdout)
            if not bugs:
                continue
            # Build PM analyze message
            bug_lines = "\n".join(f"  #{b[0]}：{b[1][:60]}" for b in bugs[:10])
            self.redis.rpush(self.cfg.redis_stream, json.dumps({
                "agent_id": "liubei",
                "message": f"请分析并分派以下 {len(bugs)} 个人类 Bug（{human}）：\n{bug_lines}",
                "source": "pm_analyze",
                "sender_id": "coordinator",
                "chat_id": "",
                "is_dm": "true",
                "msg_id": f"human-batch-{int(time.time())}",
                "timestamp": datetime.now().isoformat(),
            }))
            logger.info("[liubei] Injected %d bugs from %s into PM analysis", len(bugs), human)

    def _maybe_trigger_pipeline(self, message: str, reply: str, task: dict, redis_id: str):
        """After Hermes/LLM replies, check if user message asks for pipeline actions."""
        if self.agent_id != "liubei":
            return

        # Only trigger distribution when user explicitly says "开始分配" or "全部分配" or "执行分配"
        # NOT when Hermes analyzes/talks about bugs in conversation
        trigger_words = ["开始分配", "全部分配", "执行分配", "立刻分配", "立即分配",
                        "马上分配", "把所有bug分配", "把所有Bug分配"]
        if not any(w in message for w in trigger_words):
            return

        import re as _re
        name_map = {"王怡哲": "wangyizhe", "陈显精": "chenxj",
                    "shiyiming": "shiyiming", "史一鸣": "shiyiming",
                    "杨科祥": "yangkexiang", "yangkexiang": "yangkexiang"}
        target = None
        for name, account in name_map.items():
            if name in message or name in reply:
                target = account
                break
        if target:
            logger.info("[liubei] Pipeline trigger (explicit): distributing bugs for %s", target)
            self.reply_feishu(f"📊 **收到执行命令**\n\n正在查询 {target} 名下 Bug 并分派给智能体处理...")
            self._handle_human_bug_distribution(target)

    def _trim_stream(self):
        """Remove stream messages older than 1 hour to prevent unbounded growth."""
        try:
            cutoff = int((time.time() - 3600) * 1000)  # 1 hour ago in ms
            deleted = self.redis.xtrim(self.cfg.redis_stream, minid=str(cutoff) + "-0", approximate=True)
            if deleted:
                logger.debug("[%s] Stream trimmed: %s messages", self.agent_id, deleted)
        except Exception as e:
            logger.debug("[%s] Stream trim skipped: %s", self.agent_id, e)

    def ack(self, redis_id: str):
        pass  # BLPOP auto-removes, no ACK needed

    # =========================================================================
    #  Boot check
    # =========================================================================

    def boot_check(self):
        # Liubei (PM) doesn't fix bugs — skip boot check
        if self.agent_id == "liubei":
            logger.info("[%s] Skipping boot check (PM role, no bug fixing)", self.agent_id)
            return

        # Coordinator: zhugeliang scans ALL agent bugs and distributes
        if self.coordinator:
            logger.info("[%s] Running coordinator scan...", self.agent_id)
            dispatched = self.coordinator.scan_and_dispatch(min_interval=0)
            if dispatched > 0:
                logger.info("[%s] Coordinator distributed %d bugs.", self.agent_id, dispatched)
            # Still run own boot_check for zhugeliang's bugs
            logger.info("[%s] Performing own boot check...", self.agent_id)
        else:
            logger.info("[%s] Performing boot check...", self.agent_id)

        try:
            self._refresh_token()
            rc, out, _ = run_script(
                self.cfg.zentao_scripts_dir / "zentao-my-bugs.sh",
                self.agent_id, "active", timeout=60,
            )
            if rc != 0 or not out:
                return
            if "名下没有未解决的 Bug" in out or "当前所有任务已完成" in out:
                logger.info("[%s] No active bugs found.", self.agent_id)
                return

            bug_ids = list(set(re.findall(r"#(\d{2,4})", out)))
            if not bug_ids:
                return

            bug_list = "\n".join(f"- {b}" for b in bug_ids[:5])
            self.reply_feishu(
                f"🕵️ **开机自检报告**\n\n"
                f"我是 {self.agent_name}。\n启动后自动扫描了禅道，发现名下还有 **{len(bug_ids)}** 个未解决的 Bug：\n"
                f"{bug_list}\n\n🚀 正在进入自动修复模式..."
            )

            dispatched = 0
            self._refresh_token()
            for bid in bug_ids[:3]:
                qr, qo, _ = run_script(
                    self.cfg.zentao_scripts_dir / "zentao-bug-query.sh", bid, timeout=15,
                )
                if qr != 0 or "401" in (qo or "") or "Authorization" in (qo or ""):
                    logger.warning("[%s] Bug query failed for #%s, refreshing and retrying", self.agent_id, bid)
                    self._refresh_token()
                    qr, qo, _ = run_script(
                        self.cfg.zentao_scripts_dir / "zentao-bug-query.sh", bid, timeout=15,
                )
                if qr != 0:
                    logger.warning("[%s] Bug #%s query failed (rc=%d), skipping", self.agent_id, bid, qr)
                    continue
                bug_title = "Unknown"
                bug_reporter = "未知"
                if qo:
                    tm = re.search(r'Title:\s*(.*)', qo)
                    if tm:
                        bug_title = tm.group(1).strip()[:50]
                    rm = re.search(r'创建人:\s*(.*)', qo)
                    if rm:
                        bug_reporter = rm.group(1).strip()

                # Spawn sub-agent for parallel fixing (not via Redis)
                self.subpool.submit(self.subctx, bid, bug_title, bug_reporter)
                dispatched += 1

            logger.info("[%s] Boot check done. Spawned %d sub-agents.", self.agent_id, dispatched)
        except Exception as e:
            logger.error("[%s] Boot check failed: %s", self.agent_id, e)

    # =========================================================================
    #  Main loop
    # =========================================================================

    def run(self):
        # Each fix agent has its own dedicated queue — no race
        fix_queues = {"zhaoyun": self.cfg.redis_stream + ":fix:zhaoyun",
                      "guanyu": self.cfg.redis_stream + ":fix:guanyu",
                      "xunyu": self.cfg.redis_stream + ":fix:xunyu"}
        if self.agent_id in fix_queues:
            stream = fix_queues[self.agent_id]
        else:
            stream = self.cfg.redis_stream
        self.boot_check()
        logger.info("[%s] Main loop started (stream=%s)", self.agent_id, stream)

        last_status = 0
        while True:
            try:
                # If another agent holds the Claude Code lock, wait before polling
                if self.redis.exists("claude_code_lock"):
                    time.sleep(5)
                    continue
                result = self.redis.blpop(stream, timeout=10)
                if result:
                    # Double-check: if someone beat us to the lock, re-queue and wait
                    if self.redis.exists("claude_code_lock"):
                        self.redis.rpush(stream, result[1])
                        time.sleep(3)
                        continue
                    _, raw = result
                    task = json.loads(raw)
                    try:
                        self.handle_task(task)
                    except Exception as e:
                        logger.error("[%s] Task error: %s", self.agent_id, e)
                        traces.log(self.agent_id, "error", message=str(e)[:200], status="error")
                        try:
                            from agentforge.core.dead_letter import dead_letter
                            dead_letter.enqueue(task, str(e)[:300])
                        except Exception:
                            pass

                # Periodic sub-agent status (every 30s)
                now_ts = time.time()
                if now_ts - last_status > 30:
                    active = self.subpool.active_count
                    if active > 0:
                        logger.info("[%s] Sub-agents active: %d", self.agent_id, active)
                    last_status = now_ts

                # Periodic coordinator scan (every 5 min, zhugeliang only)
                if self.coordinator:
                    self.coordinator.scan_and_dispatch(min_interval=300)

            except KeyboardInterrupt:
                logger.info("[%s] Shutting down...", self.agent_id)
                self.subpool.shutdown(wait=False)
                break
            except Exception as e:
                logger.error("[%s] Loop error: %s", self.agent_id, e)
                time.sleep(5)
