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
        msg_type = getattr(msg_obj, "message_type", "unknown")
        if msg_type != "text":
            print(f"[WS:{AGENT_ID}] Skipped non-text message: type={msg_type}")
            return
        content = json.loads(msg_obj.content)
        text = content.get("text", "").strip()
        if not text:
            return

        chat_id = getattr(msg_obj, "chat_id", "unknown")
        chat_type = getattr(msg_obj, "chat_type", "unknown")
        # Log mentions for debugging
        mentions = getattr(msg_obj, "mentions", None) or []
        mention_names = [m.name for m in mentions if hasattr(m, 'name')]
        print(f"[WS:{AGENT_ID}] {text[:20]}... | {chat_type} | {chat_id} | mentions={mention_names}")

        is_dm = chat_type == "p2p"
        target = AGENT_ID if is_dm else None

        if not is_dm:
            # Route by Feishu native mentions (accurate @mention detection)
            mentions = getattr(msg_obj, "mentions", None) or []
            if mentions:
                # Check if any mentioned user matches an agent
                for m in mentions:
                    m_name = getattr(m, 'name', '')
                    m_id = getattr(m, 'id', {}).get('open_id', '') if isinstance(getattr(m, 'id', None), dict) else ''
                    for aid, name in AGENT_NAMES.items():
                        if name == m_name or m_id == aid:
                            target = aid
                            print(f"[WS] Mention route: {m_name} → {aid}")
                            break
                    if target:
                        break
            
            # Fallback: @所有人 → broadcast
            if not target and ("@所有人" in text or "@_user_1" in text):
                target = "broadcast"

        if target is not None:
            r = _get_redis()
            r.rpush(REDIS_STREAM, json.dumps({
                "agent_id": target, "message": text, "source": "ws_listener",
                "sender_id": ctx.event.sender.sender_id.open_id,
                "chat_id": chat_id, "is_dm": "true" if is_dm else "false",
                "msg_id": msg_obj.message_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))
    except Exception as e:
        print(f"[WS:{AGENT_ID}] Error: {e}")


def main():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)
    app_id = creds["agents"][AGENT_ID]["appId"]
    app_secret = creds["agents"][AGENT_ID]["appSecret"]
    print(f"WS Listener starting for: {AGENT_ID}...")

    import threading
    last_msg_time = time.time()

    def heartbeat_checker():
        while True:
            time.sleep(60)
            if time.time() - last_msg_time > 600:  # 10 min no message
                print(f"[WS:{AGENT_ID}] No message for 10min, triggering reconnect...")
                os._exit(0)  # Force restart via systemd Restart=always

    threading.Thread(target=heartbeat_checker, daemon=True).start()

    while True:
        try:
            print(f"[WS:{AGENT_ID}] Connecting to Feishu WebSocket...")
            cli = lark.ws.Client(
                app_id=app_id, app_secret=app_secret,
                log_level=lark.LogLevel.WARNING,
                event_handler=lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(handle_message).build(),
            )
            cli.start()
        except Exception as e:
            print(f"[WS:{AGENT_ID}] Connection lost: {e}, reconnecting in 30s...")
            time.sleep(30)


if __name__ == "__main__":
    main()
