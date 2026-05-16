"""
Feishu (Lark) API wrapper — token management and message sending.

Uses requests library directly (no more curl + tempfile).
"""

import json
import requests


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, group_chat_id: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.group_chat_id = group_chat_id
        self._token_cache = None

    def get_token(self) -> str | None:
        if self._token_cache:
            return self._token_cache
        try:
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
        except Exception:
            return None

    def send(self, text: str, target_id: str = "", id_type: str = "chat_id",
             agent_name: str = "Agent") -> bool:
        """Send an interactive card message to Feishu."""
        token = self.get_token()
        if not token:
            return False

        target = target_id or self.group_chat_id
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": f"{agent_name} 智能体"}
                ]},
            ],
        }
        payload = {
            "receive_id": target,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

        try:
            resp = requests.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages"
                f"?receive_id_type={id_type}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                import logging
                logging.getLogger("agentforge.feishu").warning(
                    "Feishu send failed: status=%d body=%s", resp.status_code, resp.text[:200]
                )
            return resp.status_code == 200
        except Exception as e:
            import logging
            logging.getLogger("agentforge.feishu").warning("Feishu send exception: %s", e)
            return False
