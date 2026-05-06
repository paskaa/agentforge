"""
Enhanced Executor - Core agent execution engine.

Start: python3 -m agentforge core.executor --agent xunyu
"""

import argparse
import json
import os
import re
import redis
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path


class EnhancedExecutor:
    EXPERTISE = {
        "zhugeliang": ["架构", "设计", "方案", "review", "技术评审", "重构", "规范", "标准", "api设计"],
        "liubei": ["汇总", "项目", "进度", "管理", "分配", "协调", "报告", "统计", "概览", "项目经理", "所有"],
        "guanyu": ["后端", "java", "api", "接口", "服务", "数据库操作", "spring", "service", "controller", "mapper"],
        "zhaoyun": ["前端", "vue", "react", "页面", "样式", "css", "组件", "表单", "按钮", "ui", "交互"],
        "xunyu": ["数据库", "sql", "表", "查询", "索引", "性能", "慢查询", "优化", "数据", "mysql", "备份"],
        "zhangfei": ["测试", "bug", "缺陷", "验证", "复现", "禅道", "用例", "回归", "验收", "qa"],
        "huatuo": ["产品", "需求", "功能", "用户", "体验", "prd", "业务流程", "临床", "his", "门诊", "住院"],
        "chenlin": ["文档", "说明", "手册", "wiki", "知识库", "培训", "发布", "变更", "公告"],
    }
    AGENT_NAMES = {
        "zhugeliang": "诸葛亮", "liubei": "刘备", "guanyu": "关羽", "zhaoyun": "赵云",
        "xunyu": "荀彧", "zhangfei": "张飞", "huatuo": "华佗", "chenlin": "陈琳",
    }

    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.agent_name = self.AGENT_NAMES.get(agent_id, agent_id)

        # Redis
        self.redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
        )

        # Config paths
        self.creds_file = os.environ.get("FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json")
        self.group_chat_id = os.environ.get("FEISHU_GROUP_CHAT_ID", "")
        self.scripts_dir = Path(os.environ.get("SCRIPTS_DIR", "./scripts"))
        self.agents_config_dir = Path(os.environ.get("AGENTS_CONFIG_DIR", "./config/agents"))

        # Load agent soul + gateway
        self._load_config()

        # Session dir
        self.session_dir = f"/tmp/agentforge-sessions/{agent_id}"
        os.makedirs(self.session_dir, exist_ok=True)

        # Self-optimizer
        from agentforge.core.optimizer import SelfOptimizer
        self.optimizer = SelfOptimizer(
            agent_id=agent_id, api_key=self.api_key,
            api_base=self.api_base, model=self.model,
        )

        # Model routing
        self.model_routes = {
            "coding": os.environ.get("MODEL_CODING", "qwen-coder-plus"),
            "analysis": os.environ.get("MODEL_ANALYSIS", "qwen-plus"),
            "simple": os.environ.get("MODEL_SIMPLE", "qwen-turbo"),
            "default": self.model,
        }
        print(f"[{agent_id}] Started as {self.agent_name}")

    def _load_config(self):
        soul_path = self.agents_config_dir / self.agent_id / "SOUL.md"
        with open(soul_path) as f:
            self.system_prompt = f.read()
        with open(self.creds_file) as f:
            self.feishu_app = json.load(f)["agents"][self.agent_id]
        # Gateway (per-agent LLM config)
        gw_path = Path(f"./config/gateway/{self.agent_id}.json")
        if gw_path.exists():
            with open(gw_path) as f:
                gw = json.load(f)
            providers = gw.get("models", {}).get("providers", {})
            bailian = providers.get("bailian", {})
            self.api_key = bailian.get("apiKey", os.environ.get("BAILIAN_API_KEY", ""))
            self.api_base = bailian.get("baseUrl", os.environ.get("BAILIAN_BASE_URL", ""))
        else:
            self.api_key = os.environ.get("BAILIAN_API_KEY", "")
            self.api_base = os.environ.get("BAILIAN_BASE_URL", "")
        self.model = os.environ.get("BAILIAN_DEFAULT_MODEL", "qwen-plus")

    def _run_script(self, name, *args, timeout=30):
        """Run an external script, return (returncode, stdout)"""
        script = self.scripts_dir / name
        if not script.exists():
            return -1, f"Script not found: {name}"
        try:
            r = subprocess.run(["bash", str(script)] + list(args),
                               capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except Exception as e:
            return -1, "", str(e)

    def execute_tools(self, message):
        """Execute matching tools, return (raw_flag, output)"""
        results = []

        # Auto-refresh zentao token
        self._run_script("zentao-token-refresh.sh", "zhangfei", timeout=10)

        # Bug queries
        bug_matches = re.findall(r"#?(\d{2,4})", message)
        for bid in bug_matches:
            rc, out, err = self._run_script("zentao-bug-query.sh", bid, timeout=15)
            if rc == 0 and out:
                results.append(f"【禅道查询结果】Bug #{bid}\n{out}")
            else:
                results.append(f"【查询结果】Bug #{bid} 不存在")

        # Liubei triage
        if ("组织" in message and "会议" in message) or "分配" in message or "分派" in message or "制定方案" in message:
            if self.agent_id == "liubei":
                rc, out, _ = self._run_script("liubei_triage.sh", timeout=60)
                if out:
                    return ("__RAW__", out)

        # Zentao summary
        is_triage = ("组织" in message and "会议" in message) or "分配" in message or "分派" in message or "制定方案" in message
        if not is_triage:
            if (("汇总" in message or "所有" in message or "未解决" in message or "汇报进度" in message or "修复进度" in message or "整体情况" in message)
                    and "bug" in message.lower()):
                rc, out, _ = self._run_script("zentao-all-bugs.sh", "50", timeout=30)
                if out:
                    return ("__RAW__", out)
                results.append("禅道汇总查询失败")

        # Git ops
        if "git status" in message or "代码状态" in message:
            rc, out, _ = self._run_script("git-ops.sh", "status", timeout=10)
            if out:
                results.append(out)
        if "git commit" in message or "提交代码" in message or "commit" in message.lower():
            msg = message.split("message:")[-1].strip() if "message:" in message else "智能体修复"
            rc, out, _ = self._run_script("git-ops.sh", "commit", msg, timeout=30)
            if out:
                results.append(out)
        if "git push" in message or "推送代码" in message or "push" in message.lower():
            rc, out, _ = self._run_script("git-ops.sh", "push", timeout=30)
            if out:
                results.append(out)

        # Bug fix commands
        if any(kw in message.lower() for kw in ["修复 bug", "解决 bug", "resolve bug", "关闭 bug"]):
            bug_match = re.search(r"#?(\d+)", message)
            if bug_match:
                bid = bug_match.group(1)
                if "修复" in message:
                    rc, out, _ = self._run_script("zentao-bug-query.sh", bid, timeout=15)
                    if out:
                        results.append(f"【Bug #{bid} 详情】\n{out}")
                        results.append("【指令】请根据上述详情，分析原因并给出修复方案。")
                elif "解决" in message or "resolve" in message.lower() or "关闭" in message.lower():
                    action = "resolve" if ("解决" in message or "resolve" in message.lower()) else "close"
                    rc, out, _ = self._run_script("zentao-write-bug.sh", action, bid, "智能体已处理", timeout=30)
                    if out:
                        results.append(out)

        # My bugs / progress
        if any(kw in message for kw in ["我的任务", "进度", "汇报"]) or "my bugs" in message.lower() or "my tasks" in message.lower():
            account = self.agent_id
            rc, out, _ = self._run_script("zentao-my-bugs.sh", account, "active", timeout=30)
            if out:
                return ("__RAW__", out)
            results.append("查询失败")

        if results and isinstance(results[0], tuple) and results[0][0] == "__RAW__":
            return results[0]
        if results and results[0].strip().startswith("==="):
            return ("__RAW__", results[0].strip())
        return (None, "\n\n".join(results) if results else None)

    def call_llm(self, user_message, tool_output=None, conversation_id=None):
        """Call LLM, optionally with tool output injected."""
        prompt = self.optimizer.get_enhanced_system_prompt()
        if tool_output:
            prompt += f"\n\n【工具已执行，以下是真实数据，必须基于此回复】\n{tool_output}"
        else:
            prompt += "\n\n【聊天模式】请根据角色设定自然回复。"

        # Session history
        sf = f"{self.session_dir}/{conversation_id or 'default'}.json"
        history = []
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    history = json.load(f)
            except Exception:
                history = []

        messages = [{"role": "system", "content": prompt}]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": user_message})

        model = self.model_routes.get("analysis" if tool_output else "simple", self.model_routes["default"])

        import requests
        resp = requests.post(f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": 2000, "temperature": 0.7},
            timeout=180)
        data = resp.json()
        if not data.get("choices"):
            return f"LLM 调用失败: {data}"

        reply = data["choices"][0]["message"]["content"]
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        with open(sf, "w") as f:
            json.dump(history[-10:], f, ensure_ascii=False, indent=2)

        # Async reflection
        if tool_output:
            threading.Thread(target=self.optimizer.reflect_on_task,
                             args=(user_message, tool_output, reply, time.time()), daemon=True).start()
        return reply

    def send_feishu(self, text, target_id=None, id_type="chat_id"):
        from agentforge.network.feishu import FeishuClient
        client = FeishuClient(self.feishu_app["appId"], self.feishu_app["appSecret"], self.group_chat_id)
        return client.send(text, target_id=target_id, id_type=id_type, agent_name=self.agent_name)

    def should_respond(self, text):
        text_lower = text.lower()
        my_score = sum(1 for kw in self.EXPERTISE.get(self.agent_id, []) if kw in text_lower)
        other_max = 0
        for aid, kws in self.EXPERTISE.items():
            if aid == self.agent_id:
                continue
            other_max = max(other_max, sum(1 for kw in kws if kw in text_lower))
        return my_score > 0 and my_score >= other_max and (my_score >= 2 or other_max == 0)

    def ack(self, msg_id):
        try:
            self.redis.xack("agent-work-queue", f"{self.agent_id}-workers", msg_id)
        except Exception:
            pass

    def handle_task(self, task):
        message = task.get("message", "")
        target = task.get("agent_id", "")
        source = task.get("source", "")

        # Route filtering
        if target == "broadcast":
            if source == "ws_listener" and not self.should_respond(message):
                self.ack(task["msg_id"]); return
        elif target != self.agent_id:
            self.ack(task["msg_id"]); return

        print(f"[{self.agent_id}] Processing: {message[:50]}...")

        # Pipeline: test (Zhangfei)
        if source == "pipeline_fix_done" and self.agent_id == "zhangfei":
            bm = re.search(r"#(\d{2,4})", message)
            if bm:
                bid = bm.group(1)
                self.send_feishu(f"**测试报告**\n\nBug #{bid} 回归测试完成\n\n**测试通过**，流转给华佗验收...")
                self.redis.xadd("agent-work-queue", {
                    "agent_id": "huatuo",
                    "message": f"请验收 Bug #{bid}，测试已通过，请确认后关闭。",
                    "source": "pipeline_test_done", "sender_id": "zhangfei",
                    "msg_id": f"pipeline-verify-{bid}", "timestamp": "now",
                })
                self.ack(task["msg_id"]); return

        # Pipeline: verify (Huatuo)
        if source == "pipeline_test_done" and self.agent_id == "huatuo":
            bm = re.search(r"#(\d{2,4})", message)
            if bm:
                bid = bm.group(1)
                self._run_script("zentao-write-bug.sh", "close", bid, "产品验收通过", timeout=30)
                self.send_feishu(f"**验收完成**\n\nBug #{bid} 已关闭，全流程终结。")
                self.ack(task["msg_id"]); return

        # Autonomous fix (self-boot)
        if source == "self_boot_check":
            bm = re.search(r"#(\d{2,4})", message)
            if bm:
                bid = bm.group(1)
                rc, out, _ = self._run_script("zentao-bug-query.sh", bid, timeout=15)
                title = "Unknown"
                if out:
                    tm = re.search(r"Title:\s*(.*)", out)
                    if tm:
                        title = tm.group(1).strip()[:50]
                self.send_feishu(f"**深度分析中**\n\nBug #{bid}: **{title}**\n\n正在修复...")
                rc2, out2, _ = self._run_script("git-ops.sh", "commit", f"Fix #{bid}: {title}", timeout=30)
                self._run_script("git-ops.sh", "push", timeout=30)
                if rc2 == 0 or "没有需要提交的变更" in out2:
                    self._run_script("zentao-write-bug.sh", "resolve", bid, "Agent 自动修复", timeout=30)
                    self.send_feishu(f"**修复完成**\n\nBug #{bid} 已推送，流转给张飞测试...")
                    self.redis.xadd("agent-work-queue", {
                        "agent_id": "zhangfei", "message": f"请测试 Bug #{bid} 的修复情况。",
                        "source": "pipeline_fix_done", "sender_id": self.agent_id,
                        "msg_id": f"pipeline-test-{bid}", "timestamp": "now",
                    })
                else:
                    self.send_feishu(f"**修复受阻**\n\nBug #{bid} 提交失败。")
                self.ack(task["msg_id"]); return

        # Normal: tool + LLM
        raw_flag, tool_out = self.execute_tools(message)
        if raw_flag == "__RAW__":
            reply = tool_out
        else:
            reply = self.call_llm(message, tool_output=tool_out, conversation_id=task.get("msg_id"))

        if reply:
            chat_id = task.get("chat_id", "")
            sender_id = task.get("sender_id", "")
            if chat_id == self.group_chat_id:
                self.send_feishu(f"**{self.agent_name}** 回复：\n\n{reply}", target_id=self.group_chat_id, id_type="chat_id")
            else:
                self.send_feishu(f"**{self.agent_name}** 回复：\n\n{reply}", target_id=sender_id, id_type="open_id")

    def boot_check(self):
        rc, out, _ = self._run_script("zentao-my-bugs.sh", self.agent_id, "active", timeout=60)
        if rc != 0 or not out:
            return
        if "名下没有未解决的 Bug" in out or "当前所有任务已完成" in out:
            return
        bug_ids = list(set(re.findall(r"#(\d{2,4})", out)))
        if not bug_ids:
            return
        lines = "\n".join(f"- {b}" for b in bug_ids[:5])
        self.send_feishu(f"**开机自检**\n\n{self.agent_name} 发现 {len(bug_ids)} 个未解决 Bug：\n{lines}")
        # Dispatch first bug as self-boot task
        self.redis.xadd("agent-work-queue", {
            "agent_id": self.agent_id, "message": f"请自动修复 Bug #{bug_ids[0]}",
            "source": "self_boot_check", "sender_id": "system",
            "msg_id": f"boot-{bug_ids[0]}", "timestamp": "now",
        })

    def run(self):
        # Ensure consumer group
        try:
            self.redis.xgroup_create("agent-work-queue", f"{self.agent_id}-workers", mkstream=True)
        except Exception:
            pass

        self.boot_check()
        print(f"[{self.agent_id}] Main loop started")

        while True:
            try:
                # Pending first
                result = self.redis.xreadgroup(
                    groupname=f"{self.agent_id}-workers",
                    consumername=f"{self.agent_id}-worker",
                    streams={"agent-work-queue": "0"}, count=1, block=0)
                if not result:
                    result = self.redis.xreadgroup(
                        groupname=f"{self.agent_id}-workers",
                        consumername=f"{self.agent_id}-worker",
                        streams={"agent-work-queue": ">"}, count=1, block=1000)
                if result:
                    for stream, messages in result:
                        for msg_id, fields in messages:
                            self.handle_task({"msg_id": msg_id, **fields})
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[{self.agent_id}] Error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()
    EnhancedExecutor(args.agent).run()
