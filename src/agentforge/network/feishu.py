"""
Feishu (Lark) API wrapper - token management and message sending.
"""

import json
import os
import subprocess
import tempfile
import requests


class FeishuClient:
    def __init__(self, app_id, app_secret, group_chat_id=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.group_chat_id = group_chat_id
        self._token_cache = None

    def get_token(self):
        if self._token_cache:
            return self._token_cache
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        token = resp.json().get("tenant_access_token")
        if token:
            self._token_cache = token
        return token

    def send(self, text, target_id=None, id_type="chat_id", agent_name="Agent"):
        token = self.get_token()
        if not token:
            return False

        target = target_id or self.group_chat_id
        card = json.dumps({
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"{agent_name} 智能体"}]},
            ],
        })
        payload = json.dumps({
            "receive_id": target, "msg_type": "interactive", "content": card,
        })

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(payload)
            tmpfile = f.name

        cmd = (f"curl -s -X POST 'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}' "
               f"-H 'Authorization: Bearer {token}' "
               f"-H 'Content-Type: application/json; charset=utf-8' "
               f"-d @{tmpfile} --connect-timeout 5 --max-time 10")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        os.remove(tmpfile)
        return result.returncode == 0
