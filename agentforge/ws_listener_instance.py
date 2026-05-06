#!/usr/bin/env python3
"""
AgentForge WebSocket Listener (Per-Agent Instance)

Each agent runs its own WS listener instance. Receives messages from
Feishu via WebSocket and pushes to Redis Stream for the executor.

Start: python3 -m agentforge.ws_listener_instance --agent xunyu
"""

import sys
import os
import json
import time
import redis
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws import *

# Agent ID from command line
AGENT_ID = sys.argv[2] if len(sys.argv) > 1 and sys.argv[1] == "--agent" else sys.argv[1] if len(sys.argv) > 1 else "unknown"

# Config from environment
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_STREAM = "agent-work-queue"
CREDENTIALS_FILE = os.environ.get(
    "FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json"
)


def handle_message(ctx):
    """Handle incoming Feishu message for this agent"""
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

        print(
            f"[WS:{AGENT_ID}] Message: {text[:15]}... | "
            f"Type: {chat_type} | ChatID: {chat_id}"
        )

        target_agent = None
        is_dm = chat_type == "p2p"

        if is_dm:
            # Direct message -> always route to this agent
            target_agent = AGENT_ID
        else:
            # Group chat -> check for @mention of this agent's name
            agent_names = {
                "zhugeliang": "诸葛亮",
                "liubei": "刘备",
                "guanyu": "关羽",
                "zhaoyun": "赵云",
                "xunyu": "荀彧",
                "zhangfei": "张飞",
                "huatuo": "华佗",
                "chenlin": "陈琳",
            }
            my_name = agent_names.get(AGENT_ID, AGENT_ID)
            if my_name in text or AGENT_ID in text:
                target_agent = AGENT_ID

        if target_agent == AGENT_ID:
            r = redis.Redis(
                host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
            )
            r.xadd(
                REDIS_STREAM,
                {
                    "agent_id": AGENT_ID,
                    "message": text,
                    "source": "ws_listener",
                    "sender_id": ctx.event.sender.sender_id.open_id,
                    "chat_id": chat_id,
                    "is_dm": "true" if is_dm else "false",
                    "msg_id": msg_obj.message_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )
            print(f"[WS:{AGENT_ID}] Pushed to Redis with ChatID: {chat_id}")
    except Exception as e:
        print(f"[WS:{AGENT_ID}] Error: {e}")


def main():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)

    app_id = creds["agents"][AGENT_ID]["appId"]
    app_secret = creds["agents"][AGENT_ID]["appSecret"]

    print(f"WS Listener Instance starting for: {AGENT_ID}...")
    cli = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        log_level=lark.LogLevel.WARNING,
        event_handler=lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build(),
    )
    cli.start()


if __name__ == "__main__":
    main()
