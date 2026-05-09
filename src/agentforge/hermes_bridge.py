#!/usr/bin/env python3
"""
Hermes Agent Wrapper for AgentForge

Replaces the raw LLM call with Hermes Agent's AIAgent,
giving AgentForge agents: persistent memory, skill system, tool calling,
and self-improvement — while keeping AgentForge's Feishu + Redis I/O layer.

Usage:
    from agentforge.hermes_bridge import HermesAgentWrapper
    wrapper = HermesAgentWrapper('zhugeliang')
    reply = wrapper.run('你好，你是谁？')
"""

import os
import sys
import json
from pathlib import Path


class HermesAgentWrapper:
    """Wraps Hermes AIAgent for use inside AgentForge executor."""

    def __init__(self, agent_id, hermes_home=None):
        self.agent_id = agent_id

        # Add Hermes to path
        hermes_dir = Path(hermes_home or "/root/hermes-agent")
        if str(hermes_dir) not in sys.path:
            sys.path.insert(0, str(hermes_dir))

        # Load config
        self._load_config()

        # Hermes AIAgent instance (lazy)
        self._agent = None

    def _load_config(self):
        """Load LLM config from env or gateway files."""
        # Try gateway config first
        gw_path = Path(f"./config/gateway/{self.agent_id}.json")
        if gw_path.exists():
            with open(gw_path) as f:
                gw = json.load(f)
            providers = gw.get("models", {}).get("providers", {})
            bailian = providers.get("bailian", {})
            self.api_key = bailian.get("apiKey", "")
            self.base_url = bailian.get("baseUrl", "")
            self.model = bailian.get("model", "qwen-plus")
        else:
            self.api_key = os.environ.get("BAILIAN_API_KEY", "")
            self.base_url = os.environ.get(
                "BAILIAN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            self.model = os.environ.get("BAILIAN_DEFAULT_MODEL", "qwen-plus")

        # Load SOUL.md as system prompt
        agents_config = os.environ.get(
            "AGENTS_CONFIG_DIR", "./config/agents"
        )
        soul_path = Path(agents_config) / self.agent_id / "agent" / "SOUL.md"
        if soul_path.exists():
            with open(soul_path) as f:
                self.system_prompt = f.read()
        else:
            self.system_prompt = None

    def _create_agent(self):
        """Create Hermes AIAgent instance."""
        from run_agent import AIAgent

        ephemeral_prompt = self.system_prompt or ""
        ephemeral_prompt += "\n\n你是通过 AgentForge 框架接入飞书的多智能体之一。"

        self._agent = AIAgent(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            max_iterations=10,
            ephemeral_system_prompt=ephemeral_prompt,
            load_soul_identity=False,
            skip_memory=False,  # Use Hermes memory
            skip_context_files=True,
            quiet_mode=True,
            log_prefix=f"[{self.agent_id}] ",
        )
        return self._agent

    def run(self, user_message, conversation_history=None):
        """
        Run a conversation turn through Hermes Agent.

        Args:
            user_message: The user's message
            conversation_history: Optional list of previous messages

        Returns:
            str: The assistant's response text
        """
        if self._agent is None:
            self._create_agent()

        result = self._agent.run_conversation(
            user_message=user_message,
            conversation_history=conversation_history or [],
        )

        # Extract response from last assistant message
        for msg in reversed(result.get("messages", [])):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                # Handle list content (tool calls + text)
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            if part.get("text", "").strip():
                                return part["text"].strip()

        return ""

    def get_stats(self):
        """Get agent usage stats."""
        if self._agent is None:
            return {"status": "not_initialized"}
        return {
            "model": self.model,
            "base_url": self.base_url,
            "session_id": self._agent.session_id,
        }
