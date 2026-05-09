"""
WebSocket Listener — Receives Feishu messages, routes to agents via Redis Stream.

Per-agent instance. Each agent runs its own listener.
Start: python3 -m agentforge ws-listener --agent xunyu

Uses a persistent Redis connection instead of creating one per message.
"""

import sys
import os
import json
import time
import redis
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws import *

from agentforge.config import Config

# --- Bootstrap ---
AGENT_ID = None
for i, arg in enumerate(sys.argv):
    if arg == "--agent" and i + 1 < len(sys.argv):
        AGENT_ID = sys.argv[i + 1]
        break
if not AGENT_ID:
    # Also try from env
    AGENT_ID = os.environ.get("AGENTFORGE_AGENT_ID", "")
if not AGENT_ID:
    print("Usage: python3 -m agentforge ws-listener --agent <id>")
    sys.exit(1)

cfg = Config()
REDIS_HOST = cfg.redis_host
REDIS_PORT = cfg.redis_port
REDIS_STREAM = cfg.redis_stream
CREDENTIALS_FILE = str(cfg.feishu_credentials_file)
AGENT_NAMES = cfg.agent_names

# Persistent Redis connection — created once, reused for all messages
_redis_conn: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    return _redis_conn


def handle_message(ctx):
    try:
        msg_obj = ctx.event.message
        if msg_obj.message_type != "text":
            return
        content = json.loads(msg_obj.content)
        text = content.get("text", "").strip()
        if not text:
            return

        chat_id = getattr(msg_obj, "chat_id", "unknown")
        chat_type = getattr(msg_obj, "chat_type", "unknown")
        print(f"[WS:{AGENT_ID}] {text[:20]}... | {chat_type} | {chat_id}")

        is_dm = chat_type == "p2p"
        target = AGENT_ID if is_dm else None

        if not is_dm:
            my_name = AGENT_NAMES.get(AGENT_ID, AGENT_ID)
            if my_name in text or AGENT_ID in text:
                target = AGENT_ID

        if target == AGENT_ID:
            r = _get_redis()
            r.xadd(REDIS_STREAM, {
                "agent_id": AGENT_ID, "message": text, "source": "ws_listener",
                "sender_id": ctx.event.sender.sender_id.open_id,
                "chat_id": chat_id, "is_dm": "true" if is_dm else "false",
                "msg_id": msg_obj.message_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
    except Exception as e:
        print(f"[WS:{AGENT_ID}] Error: {e}")


def main():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)
    app_id = creds["agents"][AGENT_ID]["appId"]
    app_secret = creds["agents"][AGENT_ID]["appSecret"]
    print(f"WS Listener starting for: {AGENT_ID}...")
    cli = lark.ws.Client(
        app_id=app_id, app_secret=app_secret,
        log_level=lark.LogLevel.WARNING,
        event_handler=lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message).build(),
    )
    cli.start()


if __name__ == "__main__":
    main()
