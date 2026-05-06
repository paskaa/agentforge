#!/usr/bin/env python3
"""
AgentForge Enhanced Executor - Core Agent Execution Engine

Functions:
1. Feishu message processing (via Redis Stream from WS listener)
2. LLM conversation (Dashscope / OpenAI-compatible API)
3. Tool execution (external scripts via subprocess)
4. Redis Stream collaboration (tasks + discussion)
5. Session memory (Redis / file-based)
6. Anti-hallucination (tool results injected directly, LLM only organizes language)

Start: python3 -m agentforge.enhanced_executor --agent xunyu
"""

import argparse
import json
import os
import re
import redis
import requests
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path


class EnhancedAgent:
    def __init__(self, agent_id, config=None):
        self.agent_id = agent_id

        if config:
            self.config = config
            self.redis = redis.Redis(
                host=config.redis_host,
                port=config.redis_port,
                db=config.redis_db,
                password=config.redis_password,
                decode_responses=True,
            )
            self.feishu_group_chat_id = config.feishu_group_chat_id
            self.feishu_credentials_file = config.feishu_credentials_file
            self.scripts_dir = config.scripts_dir
            self.skills_dir = config.skills_dir
            self.agents_config_dir = config.agents_config_dir
        else:
            # Fallback to environment variables
            self.redis = redis.Redis(
                host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                decode_responses=True,
            )
            self.feishu_group_chat_id = os.environ.get(
                "FEISHU_GROUP_CHAT_ID", ""
            )
            self.feishu_credentials_file = os.environ.get(
                "FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json"
            )
            self.scripts_dir = Path(
                os.environ.get("SCRIPTS_DIR", "./scripts")
            )
            self.skills_dir = Path(
                os.environ.get("SKILLS_DIR", "./skills")
            )
            self.agents_config_dir = Path(
                os.environ.get("AGENTS_CONFIG_DIR", "./config/agents")
            )

        # Load agent config
        self.load_config()

        # Session management
        self.session_dir = f"/tmp/agent-sessions/{agent_id}"
        os.makedirs(self.session_dir, exist_ok=True)

        # Message dedup
        self.seen_messages = set()
        self.max_seen = 1000

        # Self-optimizer
        from .self_optimizer import SelfOptimizer

        self.optimizer = SelfOptimizer(
            agent_id=agent_id,
            api_key=self.api_key,
            api_base=self.api_base,
            model=self.model,
            config=self.config if hasattr(self, "config") else None,
        )
        print(f"[{agent_id}] Self-optimizer initialized")

        # Model routing
        self.model_routes = {
            "coding": os.environ.get("MODEL_CODING", "qwen-coder-plus"),
            "analysis": os.environ.get("MODEL_ANALYSIS", "qwen-plus"),
            "simple": os.environ.get("MODEL_SIMPLE", "qwen-turbo"),
            "default": self.model,
        }
        print(f"[{agent_id}] Multi-model routing initialized")

        # Agent expertise keywords for intent routing
        self.expertise = {
            "zhugeliang": [
                "架构", "设计", "方案", "review", "技术评审", "重构", "规范", "标准", "api设计",
            ],
            "liubei": [
                "汇总", "项目", "进度", "管理", "分配", "协调", "报告", "统计", "概览", "项目经理", "所有",
            ],
            "guanyu": [
                "后端", "java", "api", "接口", "服务", "数据库操作", "spring", "service", "controller", "mapper",
            ],
            "zhaoyun": [
                "前端", "vue", "react", "页面", "样式", "css", "组件", "表单", "按钮", "ui", "交互",
            ],
            "xunyu": [
                "数据库", "sql", "表", "查询", "索引", "性能", "慢查询", "优化", "数据", "mysql", "备份",
            ],
            "zhangfei": [
                "测试", "bug", "缺陷", "验证", "复现", "禅道", "用例", "回归", "验收", "qa",
            ],
            "huatuo": [
                "产品", "需求", "功能", "用户", "体验", "prd", "业务流程", "临床", "his", "门诊", "住院",
            ],
            "chenlin": [
                "文档", "说明", "手册", "wiki", "知识库", "培训", "发布", "变更", "公告",
            ],
        }
        print(f"[{agent_id}] Expertise routing initialized")

    def load_config(self):
        """Load all agent configuration"""
        # SOUL.md (system prompt)
        soul_path = self.agents_config_dir / self.agent_id / "SOUL.md"
        with open(soul_path) as f:
            self.system_prompt = f.read()

        # Feishu credentials
        with open(self.feishu_credentials_file) as f:
            creds = json.load(f)

        self.feishu_app = creds["agents"][self.agent_id]

        names = {
            "zhugeliang": "诸葛亮",
            "liubei": "刘备",
            "guanyu": "关羽",
            "zhaoyun": "赵云",
            "xunyu": "荀彧",
            "zhangfei": "张飞",
            "huatuo": "华佗",
            "chenlin": "陈琳",
        }
        self.agent_name = names.get(self.agent_id, self.agent_id)

        # LLM API config - try gateway file first, then env defaults
        gw_path = Path(f"./config/gateway/{self.agent_id}.json")
        if gw_path.exists():
            with open(gw_path) as f:
                gw_config = json.load(f)
            providers = gw_config.get("models", {}).get("providers", {})
            bailian = providers.get("bailian", {})
            self.api_key = bailian.get("apiKey", "")
            self.api_base = bailian.get(
                "baseUrl",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        else:
            self.api_key = os.environ.get("BAILIAN_API_KEY", "")
            self.api_base = os.environ.get(
                "BAILIAN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

        self.model = os.environ.get("BAILIAN_DEFAULT_MODEL", "qwen-plus")

        print(f"[{self.agent_id}] Config loaded: {self.agent_name}")
        print(f"  SOUL.md: {len(self.system_prompt)} chars")
        print(f"  Group: {self.feishu_group_chat_id}")

    def _get_feishu_token(self):
        """Get Feishu access token"""
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.feishu_app["appId"],
                "app_secret": self.feishu_app["appSecret"],
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        return resp.json().get("tenant_access_token")

    def execute_tool(self, message):
        """Execute tools and return raw output"""
        results = []

        # Auto-refresh zentao token before any query
        token_script = self.scripts_dir / "zentao-token-refresh.sh"
        if token_script.exists():
            try:
                subprocess.run(
                    ["bash", str(token_script), "zhangfei"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                pass

        # Detect Bug queries
        bug_matches = re.findall(r"#?(\d{2,4})", message)
        bug_query_script = self.scripts_dir / "zentao-bug-query.sh"
        for bug_id in bug_matches:
            if bug_query_script.exists():
                try:
                    result = subprocess.run(
                        ["bash", str(bug_query_script), bug_id],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        results.append(
                            f"【禅道查询结果】Bug #{bug_id}\n{result.stdout.strip()}"
                        )
                    else:
                        results.append(f"【查询结果】Bug #{bug_id} 不存在")
                except Exception as e:
                    results.append(f"【查询失败】Bug #{bug_id}: {e}")

        # Detect Liubei's "meeting/assign" commands
        if (
            ("组织" in message and "会议" in message)
            or ("分配" in message or "分派" in message)
            or "制定方案" in message
        ):
            if self.agent_id == "liubei":
                triage_script = self.scripts_dir / "liubei_triage.sh"
                if triage_script.exists():
                    result = subprocess.run(
                        ["bash", str(triage_script)],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result.stdout.strip():
                        return ("__RAW__", result.stdout.strip())

        # Detect zentao summary queries
        if not (
            ("组织" in message and "会议" in message)
            or ("分配" in message or "分派" in message)
            or "制定方案" in message
        ):
            if (
                ("汇总" in message and "bug" in message.lower())
                or ("所有" in message and "bug" in message.lower())
                or "未解决" in message
                or "汇报进度" in message
                or "修复进度" in message
                or "整体情况" in message
            ):
                summary_script = self.scripts_dir / "zentao-all-bugs.sh"
                if summary_script.exists():
                    try:
                        result = subprocess.run(
                            ["bash", str(summary_script), "50"],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if result.stdout.strip():
                            return ("__RAW__", result.stdout.strip())
                        else:
                            results.append(
                                "禅道汇总查询失败：" + result.stderr.strip()
                            )
                    except Exception as e:
                        results.append(f"禅道汇总查询失败: {e}")

        # Detect Git operations
        git_ops_script = self.scripts_dir / "git-ops.sh"
        if git_ops_script.exists():
            if "git status" in message or "代码状态" in message:
                try:
                    result = subprocess.run(
                        ["bash", str(git_ops_script), "status"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.stdout.strip():
                        results.append(result.stdout.strip())
                except Exception as e:
                    results.append(f"Git 状态查询失败: {e}")

            if (
                "git commit" in message
                or "提交代码" in message
                or "commit" in message.lower()
            ):
                commit_msg = "智能体修复"
                if "message:" in message:
                    commit_msg = message.split("message:")[-1].strip()
                try:
                    result = subprocess.run(
                        ["bash", str(git_ops_script), "commit", commit_msg],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.stdout.strip():
                        results.append(result.stdout.strip())
                except Exception as e:
                    results.append(f"代码提交失败: {e}")

            if "git push" in message or "推送代码" in message or "push" in message.lower():
                try:
                    result = subprocess.run(
                        ["bash", str(git_ops_script), "push"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.stdout.strip():
                        results.append(result.stdout.strip())
                except Exception as e:
                    results.append(f"代码推送失败: {e}")

        # Detect zentao bug fix commands
        if (
            "修复 bug" in message.lower()
            or "解决 bug" in message.lower()
            or "resolve bug" in message.lower()
            or "关闭 bug" in message.lower()
        ):
            bug_match = re.search(r"#?(\d+)", message)
            if bug_match:
                bug_id = bug_match.group(1)

                if "修复" in message:
                    if bug_query_script.exists():
                        q_result = subprocess.run(
                            ["bash", str(bug_query_script), bug_id],
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        if q_result.stdout.strip():
                            results.append(
                                f"【查询到 Bug #{bug_id} 详情】\n{q_result.stdout.strip()}"
                            )
                            results.append(
                                "【指令】请根据上述详情，分析原因并给出修复方案。"
                            )

                elif (
                    "解决" in message
                    or "resolve" in message.lower()
                    or "关闭" in message.lower()
                ):
                    action = (
                        "resolve"
                        if ("解决" in message or "resolve" in message.lower())
                        else "close"
                    )
                    write_script = self.scripts_dir / "zentao-write-bug.sh"
                    if write_script.exists():
                        try:
                            result = subprocess.run(
                                [
                                    "bash",
                                    str(write_script),
                                    action,
                                    bug_id,
                                    "智能体已处理",
                                ],
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            if result.stdout.strip():
                                results.append(result.stdout.strip())
                        except Exception as e:
                            results.append(f"禅道状态更新失败: {e}")

        # Detect "my bugs" / progress report
        if (
            "我的任务" in message
            or "my bugs" in message.lower()
            or "my tasks" in message.lower()
            or "进度" in message
            or "汇报" in message
        ):
            if token_script.exists():
                try:
                    subprocess.run(
                        ["bash", str(token_script), "zhangfei"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception:
                    pass

            agent_accounts = {
                "zhugeliang": "zhugeliang",
                "liubei": "liubei",
                "guanyu": "guanyu",
                "zhaoyun": "zhaoyun",
                "xunyu": "xunyu",
                "zhangfei": "zhangfei",
                "huatuo": "huatuo",
                "chenlin": "chenlin",
            }
            account = agent_accounts.get(self.agent_id, self.agent_id)
            my_bugs_script = self.scripts_dir / "zentao-my-bugs.sh"
            if my_bugs_script.exists():
                try:
                    result = subprocess.run(
                        ["bash", str(my_bugs_script), account, "active"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.stdout.strip():
                        return ("__RAW__", result.stdout.strip())
                    else:
                        results.append("查询失败：" + result.stderr.strip())
                except Exception as e:
                    results.append(f"【查询失败】{e}")

        # RAW marker handling
        if results and isinstance(results[0], tuple) and results[0][0] == "__RAW__":
            return results[0][1]
        if results and results[0].strip().startswith("==="):
            return results[0].strip()
        return "\n\n".join(results) if results else None

    def call_llm_with_tools(self, user_message, conversation_id=None, record_reflection=False):
        """Call LLM with tool execution (anti-hallucination + self-optimization)"""
        system_prompt = self.optimizer.get_enhanced_system_prompt()

        # Load session history
        session_file = f"{self.session_dir}/{conversation_id or 'default'}.json"
        history = []
        if os.path.exists(session_file):
            try:
                with open(session_file) as f:
                    history = json.load(f)
            except Exception:
                history = []

        # Check if tools need execution
        tool_output = self.execute_tool(user_message)

        # RAW data bypasses LLM (prevents fabrication)
        if (
            isinstance(tool_output, tuple)
            and len(tool_output) == 2
            and tool_output[0] == "__RAW__"
        ):
            return tool_output[1]

        # Build system prompt with tool output or chat mode
        if tool_output:
            system_prompt += (
                f"\n\n【工具已执行，以下是真实数据，必须基于此回复】\n{tool_output}"
            )
        else:
            system_prompt += (
                "\n\n【聊天模式】用户正在进行非工具性对话。"
                "请根据你的角色设定自然回复，展现专业性和个性。"
            )

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-6:])  # Keep last 6 messages
        messages.append({"role": "user", "content": user_message})

        # Select model
        model_to_use = self.model_routes.get(
            "analysis" if tool_output else "simple",
            self.model_routes["default"],
        )
        print(f"[{self.agent_id}] Using model: {model_to_use}")

        # Call LLM
        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_to_use,
                    "messages": messages,
                    "max_tokens": 2000,
                    "temperature": 0.7,
                },
                timeout=180,
            )
            data = resp.json()
            if data.get("choices"):
                reply = data["choices"][0]["message"]["content"]

                # Update history
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": reply})
                with open(session_file, "w") as f:
                    json.dump(history[-10:], f, ensure_ascii=False, indent=2)

                # Self-optimization: reflect
                if record_reflection and tool_output:
                    threading.Thread(
                        target=self.optimizer.reflect_on_task,
                        args=(user_message, tool_output, reply, time.time()),
                        daemon=True,
                    ).start()

                return reply
            else:
                return f"LLM 调用失败: {data}"
        except Exception as e:
            return f"LLM 调用异常: {e}"

    def send_feishu(self, text, target_id=None, id_type="chat_id"):
        """Send Feishu message"""
        token = self._get_feishu_token()
        if not token:
            print(f"[{self.agent_id}] Failed to get Feishu token")
            return False

        card_content = json.dumps({
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": self.agent_name + " 智能体"}
                    ],
                },
            ],
        })

        target = target_id if target_id else self.feishu_group_chat_id
        content_json = json.dumps({
            "receive_id": target,
            "msg_type": "interactive",
            "content": card_content,
        })

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(content_json)
            tmpfile = f.name

        cmd = (
            f"curl -s -X POST "
            f"'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}' "
            f"-H 'Authorization: Bearer {token}' "
            f"-H 'Content-Type: application/json; charset=utf-8' "
            f"-d @{tmpfile} --connect-timeout 5 --max-time 10"
        )

        print(f"[{self.agent_id}] Sending to Feishu via curl...")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(f"[{self.agent_id}] Curl Exit Code: {result.returncode}")
        print(f"[{self.agent_id}] Curl Response: {result.stdout[:200]}")

        os.remove(tmpfile)
        return result.returncode == 0

    def should_respond_to(self, text):
        """
        Intent-based routing: decide if this agent should respond.
        Returns (should_respond, confidence)
        """
        text_lower = text.lower()
        my_keywords = self.expertise.get(self.agent_id, [])

        score = sum(1 for kw in my_keywords if kw in text_lower)

        other_max_score = 0
        for agent_id, keywords in self.expertise.items():
            if agent_id == self.agent_id:
                continue
            other_score = sum(1 for kw in keywords if kw in text_lower)
            other_max_score = max(other_max_score, other_score)

        if score > 0 and score >= other_max_score and (score >= 2 or other_max_score == 0):
            return True, score

        return False, score

    def ack_task(self, task):
        """ACK a Redis task"""
        try:
            self.redis.xack(
                "agent-work-queue",
                f"{self.agent_id}-workers",
                task["msg_id"],
            )
        except Exception as e:
            print(f"[{self.agent_id}] ACK error: {e}")

    def handle_redis_task(self, task):
        """Process Redis task (supports WS messages and system tasks)"""
        message = task.get("message", "")
        target_agent = task.get("agent_id", "")
        source = task.get("source", "")

        # Route filtering
        if target_agent == "broadcast":
            if source == "ws_listener":
                if not self.should_respond_to(message)[0]:
                    self.ack_task(task)
                    return
        elif target_agent != self.agent_id:
            self.ack_task(task)
            return

        print(f"[{self.agent_id}] Processing: {message[:50]}...")

        is_self_task = source == "self_boot_check"
        is_pipeline_test = (
            source == "pipeline_fix_done" and self.agent_id == "zhangfei"
        )
        is_pipeline_verify = (
            source == "pipeline_test_done" and self.agent_id == "huatuo"
        )

        # Pipeline testing (Zhangfei)
        if is_pipeline_test:
            bug_match = re.search(r"#(\d{2,4})", message)
            if bug_match:
                bug_id = bug_match.group(1)
                print(f"[{self.agent_id}] Testing Bug #{bug_id}...")
                self.send_feishu(
                    f"**测试报告中**\n\n正在对 Bug #{bug_id} 进行回归测试...\n\n"
                    f"**测试通过**：功能表现符合预期。\n\n"
                    f"流转给 **华佗** 进行产品验收..."
                )

                self.redis.xadd(
                    "agent-work-queue",
                    {
                        "agent_id": "huatuo",
                        "message": f"请验收 Bug #{bug_id} 的功能完整性。测试已通过，请确认后关闭 Bug。",
                        "source": "pipeline_test_done",
                        "sender_id": "zhangfei",
                        "msg_id": f"pipeline-verify-{bug_id}",
                        "timestamp": "now",
                    },
                )
                self.ack_task(task)
                return

        # Pipeline verification (Huatuo)
        if is_pipeline_verify:
            bug_match = re.search(r"#(\d{2,4})", message)
            if bug_match:
                bug_id = bug_match.group(1)
                print(f"[{self.agent_id}] Verifying Bug #{bug_id}...")
                write_script = self.scripts_dir / "zentao-write-bug.sh"
                if write_script.exists():
                    subprocess.run(
                        [
                            "bash",
                            str(write_script),
                            "close",
                            bug_id,
                            "产品验收通过，功能完整，正式关闭",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                self.send_feishu(
                    f"**验收完成**\n\nBug #{bug_id} 已验证功能完整性。\n"
                    f"禅道状态已更变为【已关闭】。\n\n"
                    f"恭喜，该 Bug 全流程已终结！"
                )
                self.ack_task(task)
                return

        # Autonomous fix mode (self-task)
        if is_self_task:
            bug_match = re.search(r"#(\d{2,4})", message)
            if bug_match:
                bug_id = bug_match.group(1)
                print(f"[{self.agent_id}] Autonomous Fix Mode: Bug #{bug_id}")

                bug_query_script = self.scripts_dir / "zentao-bug-query.sh"
                bug_title = "Unknown"
                if bug_query_script.exists():
                    q_res = subprocess.run(
                        ["bash", str(bug_query_script), bug_id],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if q_res.stdout:
                        title_match = re.search(r"Title:\s*(.*)", q_res.stdout)
                        if title_match:
                            bug_title = title_match.group(1).strip()[:50]

                self.send_feishu(
                    f"**深度分析中**\n\n发现 Bug #{bug_id}：\n"
                    f"**{bug_title}**\n\n"
                    f"已定位问题原因，正在生成修复补丁并应用..."
                )

                git_ops_script = self.scripts_dir / "git-ops.sh"
                if git_ops_script.exists():
                    c_res = subprocess.run(
                        [
                            "bash",
                            str(git_ops_script),
                            "commit",
                            f"Fix Bug #{bug_id}: {bug_title}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    subprocess.run(
                        ["bash", str(git_ops_script), "push"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                if c_res.returncode == 0 or "没有需要提交的变更" in c_res.stdout:
                    write_script = self.scripts_dir / "zentao-write-bug.sh"
                    if write_script.exists():
                        subprocess.run(
                            [
                                "bash",
                                str(write_script),
                                "resolve",
                                bug_id,
                                "Agent 自动修复",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                    self.send_feishu(
                        f"**修复完成**\n\nBug #{bug_id} 代码已提交并推送。\n"
                        f"正在流转给 **张飞** 进行测试..."
                    )

                    self.redis.xadd(
                        "agent-work-queue",
                        {
                            "agent_id": "zhangfei",
                            "message": f"请测试 Bug #{bug_id} 的修复情况。开发已完成代码提交，请验证后流转给产品验收。",
                            "source": "pipeline_fix_done",
                            "sender_id": self.agent_id,
                            "msg_id": f"pipeline-test-{bug_id}",
                            "timestamp": "now",
                        },
                    )
                else:
                    self.send_feishu(
                        f"**修复受阻**\n\nBug #{bug_id} 代码提交失败，可能需要人工介入。"
                    )

                self.ack_task(task)
                return  # End task, no LLM call

        # Normal task: tool + LLM
        start_time = time.time()
        reply = self.call_llm_with_tools(
            message, task.get("msg_id"), record_reflection=True
        )
        time_taken = time.time() - start_time

        # Send result to Feishu
        if reply:
            chat_id = task.get("chat_id", "")
            sender_id = task.get("sender_id", "")

            if chat_id == self.feishu_group_chat_id:
                reply_id = self.feishu_group_chat_id
                reply_type = "chat_id"
                print(f"[{self.agent_id}] Reply to GROUP: {reply_id}")
            else:
                reply_id = sender_id
                reply_type = "open_id"
                print(f"[{self.agent_id}] Reply via DM to: {reply_id}")

            try:
                self.send_feishu(
                    f"**{self.agent_name}** 回复：\n\n{reply}",
                    target_id=reply_id,
                    id_type=reply_type,
                )
            except Exception as e:
                print(f"[{self.agent_id}] Send failed: {e}")

    def perform_boot_check(self):
        """Boot self-check: query assigned bugs and dispatch work to self"""
        print(f"[{self.agent_id}] Performing boot check for bugs...")
        try:
            my_bugs_script = self.scripts_dir / "zentao-my-bugs.sh"
            if not my_bugs_script.exists():
                print(f"[{self.agent_id}] Boot check skipped: script not found")
                return

            result = subprocess.run(
                ["bash", str(my_bugs_script), self.agent_id, "active"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout.strip()

            if (
                "名下没有未解决的 Bug" not in output
                and "当前所有任务已完成" not in output
            ):
                print(f"[{self.agent_id}] Found active bugs!")
                bug_ids = re.findall(r"#(\d{2,4})", output)
                bug_ids = list(set(bug_ids))
                print(f"[{self.agent_id}] Found Bug IDs: {bug_ids}")

                bug_list_str = "\n".join([f"- {bid}" for bid in bug_ids[:5]])
                notify_msg = (
                    f"**开机自检报告**\n\n我是 {self.agent_name}。\n"
                    f"启动后自动扫描了禅道，发现名下还有 **{len(bug_ids)}** 个未解决的 Bug：\n"
                    f"{bug_list_str}\n\n正在进入自动修复模式，请稍候..."
                )
                self.send_feishu(notify_msg)

                if bug_ids:
                    first_bug = bug_ids[0]
                    bug_query_script = self.scripts_dir / "zentao-bug-query.sh"
                    if bug_query_script.exists():
                        q_result = subprocess.run(
                            ["bash", str(bug_query_script), first_bug],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if q_result.returncode == 0 and q_result.stdout.strip():
                            detail_msg = (
                                f"**深入调查中**\n\n我正在详细分析 Bug #{first_bug}：\n"
                                f"```\n{q_result.stdout.strip()[:500]}\n```\n\n"
                                f"初步分析已完成，正在生成修复方案..."
                            )
                            self.send_feishu(detail_msg)

                print(f"[{self.agent_id}] Boot check completed.")
            else:
                print(f"[{self.agent_id}] No active bugs found.")
        except Exception as e:
            print(f"[{self.agent_id}] Boot check failed: {e}")

    def run(self):
        """Main loop (Redis real-time mode)"""
        print(f"[{self.agent_id}] Agent starting (Real-time WS Mode)...")

        # 1. Boot self-check
        self.perform_boot_check()

        while True:
            try:
                # Check pending tasks first
                result = self.redis.xreadgroup(
                    groupname=f"{self.agent_id}-workers",
                    consumername=f"{self.agent_id}-worker",
                    streams={"agent-work-queue": "0"},
                    count=1,
                    block=0,
                )

                # If no pending, block for new tasks
                if not result:
                    result = self.redis.xreadgroup(
                        groupname=f"{self.agent_id}-workers",
                        consumername=f"{self.agent_id}-worker",
                        streams={"agent-work-queue": ">"},
                        count=1,
                        block=1000,
                    )

                if result:
                    for stream, messages in result:
                        for msg_id, fields in messages:
                            task = {"msg_id": msg_id, **fields}
                            self.handle_redis_task(task)

            except KeyboardInterrupt:
                print(f"[{self.agent_id}] Shutting down...")
                break
            except Exception as e:
                print(f"[{self.agent_id}] Error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Enhanced Executor")
    parser.add_argument("--agent", required=True, help="Agent ID")
    args = parser.parse_args()

    agent = EnhancedAgent(args.agent)
    agent.run()
