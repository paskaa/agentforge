"""
AgentForge CLI entry point

Usage:
    python3 -m agentforge executor --agent xunyu
    python3 -m agentforge ws-listener --agent xunyu
    python3 -m agentforge scheduler
    python3 -m agentforge workflow list
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    if len(sys.argv) < 2:
        print("AgentForge - Multi-Agent Collaboration Framework")
        print()
        print("Usage:")
        print("  python3 -m agentforge executor --agent <id>")
        print("  python3 -m agentforge ws-listener --agent <id>")
        print("  python3 -m agentforge scheduler")
        print("  python3 -m agentforge workflow [list|create|run <id>]")
        print("  python3 -m agentforge skills [list|stats|find <task>]")
        sys.exit(0)

    command = sys.argv[1]

    if command == "executor":
        from agentforge.enhanced_executor import EnhancedAgent

        # Parse --agent from remaining args
        agent_id = None
        for i, arg in enumerate(sys.argv[2:]):
            if arg == "--agent" and i + 1 < len(sys.argv) - 2:
                agent_id = sys.argv[i + 3]
                break
        if not agent_id:
            print("Error: --agent <id> is required")
            sys.exit(1)

        agent = EnhancedAgent(agent_id)
        agent.run()

    elif command == "ws-listener":
        from agentforge.ws_listener_instance import main as ws_main

        ws_main()

    elif command == "scheduler":
        from agentforge.scheduler import AgentScheduler

        scheduler = AgentScheduler()
        scheduler.loop()

    elif command == "workflow":
        from agentforge.workflow_engine import WorkflowEngine, create_bug_workflow

        engine = WorkflowEngine()
        subcmd = sys.argv[2] if len(sys.argv) > 2 else None

        if subcmd == "list":
            engine.list_workflows()
        elif subcmd == "create":
            wf_id = create_bug_workflow()
            print(f"Created workflow: {wf_id}")
        elif subcmd == "run" and len(sys.argv) > 3:
            engine.run_workflow(sys.argv[3])
        else:
            print("Usage: python3 -m agentforge workflow [list|create|run <id>]")

    elif command == "skills":
        from agentforge.skill_registry import SkillRegistry

        registry = SkillRegistry()
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "stats"

        if subcmd == "list":
            skills = registry.list_all_skills()
            for sid, skill in sorted(skills.items()):
                print(f"  [{skill.get('category')}] {skill.get('name')} ({sid})")
        elif subcmd == "stats":
            stats = registry.get_statistics()
            print(f"Total skills: {stats['total']}")
            print(f"By category: {stats['by_category']}")
        elif subcmd == "find" and len(sys.argv) > 3:
            matches = registry.find_skills_for_task(" ".join(sys.argv[3:]))
            for sid, skill in matches:
                print(f"  {skill.get('name')} ({sid})")
        else:
            print("Usage: python3 -m agentforge skills [list|stats|find <task>]")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
