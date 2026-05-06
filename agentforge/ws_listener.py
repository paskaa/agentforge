#!/usr/bin/env python3
"""
AgentForge WebSocket Listener (Global) - Single dispatcher for all agents

Receives messages from Feishu via WebSocket, routes to the appropriate
agent based on @mentions or keyword matching, then pushes to Redis Stream.

Start: python3 -m agentforge.ws_listener
"""

import json
import os
import time
import redis
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws import *

# Load config from environment
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_STREAM = "agent-work-queue"
GROUP_CHAT_ID = os.environ.get("FEISHU_GROUP_CHAT_ID", "")
CREDENTIALS_FILE = os.environ.get(
    "FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json"
)

AGENT_NAMES = {
    "zhugeliang": "诸葛亮",
    "liubei": "刘备",
    "guanyu": "关羽",
    "zhaoyun": "赵云",
    "xunyu": "荀彧",
    "zhangfei": "张飞",
    "huatuo": "华佗",
    "chenlin": "陈琳",
}

INTENT_KEYWORDS = {
    "guanyu": ["后端", "java", "api", "接口", "服务"],
    "zhaoyun": ["前端", "vue", "页面", "样式", "组件"],
    "xunyu": ["数据库", "sql", "表", "查询", "性能"],
    "zhangfei": ["测试", "bug", "缺陷", "禅道"],
    "huatuo": ["产品", "需求", "功能", "用户"],
    "zhugeliang": ["架构", "设计", "方案", "review"],
    "liubei": ["汇总", "项目", "进度", "管理", "汇报", "会议", "分配"],
    "chenlin": ["文档", "说明", "手册", "wiki"],
}


def get_agent_id_by_keyword(text):
    """Route message to agent based on keyword matching"""
    text_lower = text.lower()
    scores = {}
    for agent_id, kws in INTENT_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text_lower)
        if score > 0:
            scores[agent_id] = score
    if not scores:
        return "broadcast"
    return max(scores, key=scores.get)


def get_agent_id_by_mention(mentions, text):
    """Route message to agent based on @mention"""
    try:
        for mention in mentions:
            name = mention.name if hasattr(mention, "name") else mention.get("name", "")
            for aid, aname in AGENT_NAMES.items():
                if name == aname or name == aid:
                    return aid
    except Exception:
        pass
    return None


def handle_message(ctx):
    """Handle incoming Feishu message"""
    try:
        if hasattr(ctx, "event"):
            event = ctx.event
        else:
            event = ctx

        if hasattr(event, "message"):
            message = event.message
        else:
            print(f"[WS] Unknown event structure: {type(event)}")
            return

        # Filter by group
        if message.chat_id != GROUP_CHAT_ID:
            return

        # Filter non-text
        if message.message_type != "text":
            return

        # Filter bot messages
        sender_type = "unknown"
        if hasattr(event, "sender") and event.sender:
            sender_type = event.sender.sender_type
        if sender_type == "app":
            return

        # Parse content
        content_str = message.content
        content = json.loads(content_str)
        text = content.get("text", "").strip()

        if not text:
            return

        # Routing logic
        mentions = message.mention_list if hasattr(message, "mention_list") else []
        target_agent = get_agent_id_by_mention(mentions, text)
        if not target_agent:
            target_agent = get_agent_id_by_keyword(text)

        sender_id = "unknown"
        if hasattr(event, "sender") and event.sender:
            sender_id = (
                event.sender.sender_id.open_id
                if hasattr(event.sender.sender_id, "open_id")
                else str(event.sender.sender_id)
            )

        print(f"[WS] -> Routing to: {target_agent} (Msg: {text[:20]}...)")

        # Push to Redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.xadd(
            REDIS_STREAM,
            {
                "agent_id": target_agent,
                "message": text,
                "source": "ws_listener",
                "sender_id": sender_id,
                "msg_id": message.message_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
    except Exception as e:
        print(f"[WS] Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)

    # Use the first agent's app credentials for WS connection
    agents = creds.get("agents", {})
    if not agents:
        print("No Feishu credentials found")
        return
    first_agent = next(iter(agents.values()))
    app_id = first_agent["appId"]
    app_secret = first_agent["appSecret"]

    cli = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        log_level=lark.LogLevel.WARNING,
        event_handler=lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build(),
    )

    print(f"WS Listener starting (App: {app_id})...")
    cli.start()


if __name__ == "__main__":
    main()
