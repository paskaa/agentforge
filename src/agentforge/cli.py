"""
AgentForge CLI entry point.

Usage:
    agentforge executor --agent xunyu
    agentforge ws-listener --agent xunyu
    agentforge scheduler
    agentforge workflow [list|create|run <id>]
    agentforge skills [list|stats|find <task>]
"""

import logging
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _setup_logging(level: str = "INFO"):
    """Configure structured logging for all agentforge modules."""
    fmt = "%(asctime)s [%(levelname)-5s] %(name)s — %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Quiet down noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def main():
    _setup_logging()
    if len(sys.argv) < 2:
        print("AgentForge - Multi-Agent Collaboration Framework")
        print("\nUsage:")
        print("  agentforge executor --agent <id>")
        print("  agentforge ws-listener --agent <id>")
        print("  agentforge scheduler")
        print("  agentforge workflow [list|create|run <id>]")
        print("  agentforge skills [list|stats|find <task>]")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "executor":
        from agentforge.config import Config
        from agentforge.core.executor import AgentExecutor

        agent_id = None
        for i, a in enumerate(sys.argv[2:]):
            if a == "--agent" and i + 1 < len(sys.argv) - 1:
                agent_id = sys.argv[i + 3]
                break
        if not agent_id:
            print("Error: --agent <id> required")
            sys.exit(1)

        cfg = Config()
        AgentExecutor(agent_id, config=cfg).run()

    elif cmd == "ws-listener":
        from agentforge.network import ws_listener as ws_mod
        sys.argv = [sys.argv[0], "network", "ws_listener"] + sys.argv[2:]
        ws_mod.main()

    elif cmd == "scheduler":
        from agentforge.config import Config
        from agentforge.tools.scheduler import Scheduler
        cfg = Config()
        Scheduler(config=cfg).loop()

    elif cmd == "workflow":
        from agentforge.workflow.engine import WorkflowEngine
        engine = WorkflowEngine()
        sub = sys.argv[2] if len(sys.argv) > 2 else None
        if sub == "list":
            engine.list_workflows()
        elif sub == "create":
            from agentforge.workflow.engine import create_bug_workflow
            print(f"Created: {create_bug_workflow()}")
        elif sub == "run" and len(sys.argv) > 3:
            engine.run(sys.argv[3])
        else:
            print("Usage: agentforge workflow [list|create|run <id>]")

    elif cmd == "skills":
        from agentforge.tools.skill_registry import SkillRegistry
        reg = SkillRegistry()
        sub = sys.argv[2] if len(sys.argv) > 2 else "stats"
        if sub == "list":
            for sid, s in sorted(reg.list_all().items()):
                print(f"  [{s.get('category')}] {s.get('name')} ({sid})")
        elif sub == "stats":
            st = reg.get_statistics()
            print(f"Total: {st['total']}\nBy category: {st['by_category']}\nBy type: {st['by_type']}")
        elif sub == "find" and len(sys.argv) > 3:
            for sid, s in reg.find_for_task(" ".join(sys.argv[3:])):
                print(f"  {s.get('name')} ({sid})")
        else:
            print("Usage: agentforge skills [list|stats|find <task>]")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
