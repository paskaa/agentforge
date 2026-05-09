"""
Tool Registry — plugin-based tool dispatch.

Replaces the giant if-else chain in execute_tools() with a
declarative registry. Each tool is a decorated function with
trigger keywords, priority, and a handler.

Tools are discovered at import time and can be added dynamically.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("agentforge.tools")


@dataclass
class ToolPlugin:
    """A registered tool plugin."""
    name: str
    description: str
    handler: Callable  # (message: str, ctx: "ToolContext") -> Optional[str]
    triggers: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    priority: int = 0          # Higher = executed first
    raw_output: bool = False   # If True, output bypasses LLM
    agent_only: Optional[str] = None  # Only fire for a specific agent_id


@dataclass
class ToolContext:
    """Context passed to tool handlers."""
    agent_id: str
    agent_name: str
    zentao_dir: Path
    scripts_dir: Path
    agent_account: str
    refresh_token: Callable[[], None]  # Call to refresh zentao token

    def z(self, script_name: str) -> Path:
        """Resolve a script path under zentao_dir."""
        return self.zentao_dir / script_name


class ToolRegistry:
    """Registry of tool plugins with keyword-based matching."""

    def __init__(self):
        self._tools: list[ToolPlugin] = []

    def register(self, tool: ToolPlugin):
        self._tools.append(tool)
        self._tools.sort(key=lambda t: -t.priority)
        logger.debug("Registered tool: %s (priority=%d)", tool.name, tool.priority)

    def find(self, message: str, agent_id: str = "") -> list[ToolPlugin]:
        """Find tools matching a message. Returns scored, sorted list."""
        text = message.lower()
        scored: list[tuple[int, ToolPlugin]] = []

        for tool in self._tools:
            if tool.agent_only and tool.agent_only != agent_id:
                continue
            score = 0
            for kw in tool.triggers:
                if kw.lower() in text:
                    score += 3
            for kw in tool.keywords:
                if kw.lower() in text:
                    score += 1
            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored]

    def execute(self, message: str, ctx: ToolContext) -> tuple[Optional[str], Optional[str]]:
        """
        Find and execute matching tools.
        Returns (raw_flag, output) where raw_flag is '__RAW__' or None.
        """
        matching = self.find(message, ctx.agent_id)
        results: list[str] = []

        for tool in matching:
            try:
                output = tool.handler(message, ctx)
                if output is not None:
                    if tool.raw_output:
                        return ("__RAW__", output)
                    results.append(output)
            except Exception as e:
                logger.error("Tool %s failed: %s", tool.name, e)
                results.append(f"【{tool.name} 执行失败】{e}")

        if not results:
            return (None, None)

        # Check if any result is a raw marker
        first = results[0]
        if first.startswith("==="):
            return ("__RAW__", first)
        return (None, "\n\n".join(results))


# =========================================================================
#  Decorator
# =========================================================================

def tool(
    name: str,
    description: str = "",
    triggers: list[str] | None = None,
    keywords: list[str] | None = None,
    priority: int = 0,
    raw_output: bool = False,
    agent_only: str | None = None,
):
    """Decorator to register a function as a tool plugin."""
    def decorator(func: Callable) -> Callable:
        func._tool_plugin = ToolPlugin(
            name=name,
            description=description,
            handler=func,
            triggers=triggers or [],
            keywords=keywords or [],
            priority=priority,
            raw_output=raw_output,
            agent_only=agent_only,
        )
        return func
    return decorator


def discover_tools(registry: ToolRegistry, module):
    """Auto-discover tools from a module and register them."""
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if callable(obj) and hasattr(obj, "_tool_plugin"):
            registry.register(obj._tool_plugin)
