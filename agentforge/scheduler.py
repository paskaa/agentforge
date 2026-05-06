#!/usr/bin/env python3
"""
AgentForge Scheduler - Cron-style Task Scheduler

Functions:
1. Scheduled bug reports (daily at 09:00)
2. Agent health checks (every 5 minutes)
3. Custom periodic tasks

Start: python3 -m agentforge.scheduler
"""

import json
import os
import time
import subprocess
import redis
import requests
from datetime import datetime
from pathlib import Path


class AgentScheduler:
    def __init__(self, config=None):
        if config:
            self.redis = redis.Redis(
                host=config.redis_host,
                port=config.redis_port,
                password=config.redis_password,
                decode_responses=True,
            )
            self.feishu_group_chat_id = config.feishu_group_chat_id
            self.feishu_credentials_file = config.feishu_credentials_file
            self.scripts_dir = config.scripts_dir
        else:
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

        self.tasks_file = "./config/scheduler_tasks.json"
        self.tasks = self._load_tasks()
        print("Scheduler initialized")

    def _load_tasks(self):
        try:
            with open(self.tasks_file) as f:
                return json.load(f)
        except Exception:
            return {
                "daily_report": {
                    "enabled": True,
                    "time": "09:00",
                    "description": "每日 Bug 汇总",
                },
                "health_check": {
                    "enabled": True,
                    "interval": 300,
                    "description": "Agent 健康检查",
                },
            }

    def _get_feishu_token(self):
        """Get Feishu access token"""
        try:
            with open(self.feishu_credentials_file) as f:
                creds = json.load(f)
            # Use the first available agent's credentials
            agents = creds.get("agents", {})
            if not agents:
                return None
            first_agent = next(iter(agents.values()))
            resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": first_agent["appId"],
                    "app_secret": first_agent["appSecret"],
                },
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            return resp.json().get("tenant_access_token")
        except Exception:
            return None

    def send_feishu(self, text):
        """Send Feishu message to group"""
        token = self._get_feishu_token()
        if not token:
            return False

        content = json.dumps({
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": "AgentForge 定时任务"}
                    ],
                },
            ],
        })

        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json={
                    "receive_id": self.feishu_group_chat_id,
                    "msg_type": "interactive",
                    "content": content,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            return resp.status_code == 200
        except Exception:
            return False

    def run_daily_report(self):
        """Execute daily bug summary"""
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Running Daily Report..."
        )

        summary_script = self.scripts_dir / "zentao-all-bugs.sh"
        if not summary_script.exists():
            print("Summary script not found")
            return

        result = subprocess.run(
            ["bash", str(summary_script), "20"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.stdout.strip():
            msg = f"每日 Bug 汇总 ({datetime.now().strftime('%Y-%m-%d')})\n\n{result.stdout.strip()}"
            self.send_feishu(msg)
            print("Daily report sent")
        else:
            print("Daily report failed")

    def run_health_check(self):
        """Execute agent health check"""
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Running Health Check..."
        )

        agents = [
            "zhugeliang",
            "liubei",
            "guanyu",
            "zhaoyun",
            "xunyu",
            "zhangfei",
            "huatuo",
            "chenlin",
        ]
        status_lines = []
        all_active = True

        for agent in agents:
            result = subprocess.run(
                ["systemctl", "is-active", f"agent-executor@{agent}"],
                capture_output=True,
                text=True,
            )
            status = result.stdout.strip()
            icon = "✅" if status == "active" else "❌"
            if status != "active":
                all_active = False
            status_lines.append(f"{icon} {agent}: {status}")

        msg = "Agent 健康检查\n\n" + "\n".join(status_lines)
        if all_active:
            msg += "\n\n所有 Agent 运行正常"
            print("All agents healthy")
        else:
            msg += "\n\n存在异常，请检查"
            self.send_feishu(msg)
            print("Health check found issues")

    def loop(self):
        """Main loop"""
        last_report_date = None
        last_health_check = 0

        print("Scheduler loop started...")

        while True:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_ts = now.timestamp()

            # Daily report at configured time
            if self.tasks.get("daily_report", {}).get("enabled"):
                report_time = self.tasks["daily_report"].get("time", "09:00")
                if current_time == report_time:
                    if last_report_date != now.strftime("%Y-%m-%d"):
                        self.run_daily_report()
                        last_report_date = now.strftime("%Y-%m-%d")

            # Health check at configured interval
            if self.tasks.get("health_check", {}).get("enabled"):
                interval = self.tasks["health_check"].get("interval", 300)
                if current_ts - last_health_check >= interval:
                    self.run_health_check()
                    last_health_check = current_ts

            time.sleep(30)


if __name__ == "__main__":
    scheduler = AgentScheduler()
    scheduler.loop()
