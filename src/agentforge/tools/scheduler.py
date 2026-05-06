"""
Scheduler - Cron-style periodic tasks (daily reports, health checks).
"""

import json
import os
import time
import subprocess
import redis
import requests
from datetime import datetime
from pathlib import Path


class Scheduler:
    def __init__(self):
        self.redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
        )
        self.group_chat_id = os.environ.get("FEISHU_GROUP_CHAT_ID", "")
        self.creds_file = os.environ.get("FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json")
        self.scripts_dir = Path(os.environ.get("SCRIPTS_DIR", "./scripts"))
        self.tasks_file = "./config/scheduler_tasks.json"
        self.tasks = self._load_tasks()

    def _load_tasks(self):
        try:
            with open(self.tasks_file) as f:
                return json.load(f)
        except Exception:
            return {
                "daily_report": {"enabled": True, "time": "09:00", "description": "每日 Bug 汇总"},
                "health_check": {"enabled": True, "interval": 300, "description": "Agent 健康检查"},
            }

    def _get_feishu_token(self):
        try:
            with open(self.creds_file) as f:
                creds = json.load(f)
            agents = creds.get("agents", {})
            if not agents:
                return None
            first = next(iter(agents.values()))
            resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": first["appId"], "app_secret": first["appSecret"]},
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            return resp.json().get("tenant_access_token")
        except Exception:
            return None

    def send_feishu(self, text):
        token = self._get_feishu_token()
        if not token:
            return False
        content = json.dumps({
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "AgentForge 定时任务"}]},
            ],
        })
        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json={"receive_id": self.group_chat_id, "msg_type": "interactive", "content": content},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    def run_daily_report(self):
        script = self.scripts_dir / "zentao-all-bugs.sh"
        if not script.exists():
            return
        r = subprocess.run(["bash", str(script), "20"], capture_output=True, text=True, timeout=60)
        if r.stdout.strip():
            msg = f"每日 Bug 汇总 ({datetime.now().strftime('%Y-%m-%d')})\n\n{r.stdout.strip()}"
            self.send_feishu(msg)

    def run_health_check(self):
        agents = ["zhugeliang", "liubei", "guanyu", "zhaoyun", "xunyu", "zhangfei", "huatuo", "chenlin"]
        lines = []
        all_ok = True
        for a in agents:
            r = subprocess.run(["systemctl", "is-active", f"agentforge-executor@{a}"], capture_output=True, text=True)
            s = r.stdout.strip()
            icon = "✅" if s == "active" else "❌"
            if s != "active":
                all_ok = False
            lines.append(f"{icon} {a}: {s}")
        msg = "Agent 健康检查\n\n" + "\n".join(lines)
        msg += "\n\n所有 Agent 运行正常" if all_ok else "\n\n存在异常，请检查"
        if not all_ok:
            self.send_feishu(msg)

    def loop(self):
        last_report = None
        last_health = 0
        while True:
            now = datetime.now()
            ct = now.strftime("%H:%M")
            ts = now.timestamp()
            if self.tasks.get("daily_report", {}).get("enabled"):
                rt = self.tasks["daily_report"].get("time", "09:00")
                if ct == rt and last_report != now.strftime("%Y-%m-%d"):
                    self.run_daily_report()
                    last_report = now.strftime("%Y-%m-%d")
            if self.tasks.get("health_check", {}).get("enabled"):
                interval = self.tasks["health_check"].get("interval", 300)
                if ts - last_health >= interval:
                    self.run_health_check()
                    last_health = ts
            time.sleep(30)
