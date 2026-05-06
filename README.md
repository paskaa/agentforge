# AgentForge

> A multi-agent collaboration framework with Feishu integration, tool execution, autonomous optimization, and workflow orchestration.

AgentForge deploys multiple AI agents (each with a distinct role/persona) that collaborate through a message queue to handle real-world tasks like bug tracking, code management, project reporting, and more. Each agent connects to Feishu (Lark) for human interaction, calls LLM APIs for reasoning, and executes external tools (scripts) for real-world actions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Feishu (Lark)                             │
│              Group Chat / Direct Messages                        │
└────────────────────┬────────────────────────────────────────────┘
                     │ WebSocket
                     ▼
┌────────────────────────────────────────┐
│        WS Listener (per-agent)          │
│  Parses @mentions + keyword routing     │
└────────────────┬───────────────────────┘
                 │ Redis Stream (agent-work-queue)
                 ▼
┌────────────────────────────────────────────────────────────────┐
│                    Enhanced Executor (per-agent)                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Intent       │  │ Tool         │  │ LLM (Multi-model       │  │
│  │ Routing      │  │ Execution    │  │  Routing)              │  │
│  │ (keyword)    │  │ (subprocess) │  │  bailian API)          │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Self-Optimizer + Experience Memory            │   │
│  │  (auto-reflection, dynamic rules, performance tracking)   │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬───────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         Feishu Reply   Redis Stream   External Tools
         (group/DM)    (cross-agent)   (zentao, git)
```

## Features

- **Multi-Agent Roles** - 8 preset personas (architect, PM, backend dev, frontend dev, DBA, QA, product manager, tech writer), each with independent SOUL.md personality definitions
- **Anti-Hallucination** - Tool results marked `__RAW__` bypass the LLM entirely, preventing fabrication of bug reports, schedules, or data
- **Model Routing** - Automatically selects the best model per task type (coding → qwen-coder-plus, analysis → qwen-plus, simple → qwen-turbo)
- **Autonomous Pipeline** - Boot self-check → bug query → git commit → push → handoff to QA → product acceptance → close
- **Self-Optimization** - Post-task reflection, experience memory, dynamic rule updates, performance tracking
- **Intent-Based Routing** - Keyword scoring across all agents determines who should respond to each message
- **Workflow Engine** - Supports serial, parallel, and approval-gated multi-step workflows
- **Skill Registry** - Discoverable, installable skills with keyword/triggers matching

## Quick Start

### Prerequisites

- Python 3.9+
- Redis
- Feishu (Lark) open platform app(s)
- Dashscope / Bailian API access (or any OpenAI-compatible API)

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/agentforge.git
cd agentforge

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and configure
cp .env.example .env
cp config/feishu_credentials.json.example config/feishu_credentials.json
```

### Configuration

1. **`.env`** - Set Redis address, API keys, Feishu group chat ID, and paths:

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
BAILIAN_API_KEY=sk-xxx
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_DEFAULT_MODEL=qwen-plus
FEISHU_GROUP_CHAT_ID=oc_xxxxxxxxxxxxxxxx
FEISHU_CREDENTIALS_FILE=./config/feishu_credentials.json
SCRIPTS_DIR=./scripts
AGENTS_CONFIG_DIR=./config/agents
```

2. **`config/feishu_credentials.json`** - Feishu app credentials for each agent:

```json
{
  "agents": {
    "zhugeliang": {
      "appId": "cli_xxx",
      "appSecret": "xxx"
    }
  }
}
```

3. **`config/agents/{agent_id}/SOUL.md`** - Personality and role definition for each agent:

```markdown
你是荀彧智能体，负责 HIS 项目的 DBA 工作。

**核心职责**：
1. 禅道 Bug 查询
2. 数据库相关技术问题处理

**严禁幻觉**：所有查询必须使用工具执行，禁止编造任何数据。
```

### Running

```bash
# Start a single agent executor
python3 -m agentforge.enhanced_executor --agent xunyu

# Start a WS listener for an agent
python3 -m agentforge.ws_listener_instance --agent xunyu

# Start the scheduler (daily reports + health checks)
python3 -m agentforge.scheduler

# List workflows
python3 -m agentforge.workflow_engine list
```

### Systemd Deployment

Copy service templates to `/etc/systemd/system/`:

```bash
sudo cp systemd/agentforge-executor@.service /etc/systemd/system/
sudo cp systemd/agentforge-ws@.service /etc/systemd/system/
sudo cp systemd/agentforge-scheduler.service /etc/systemd/system/

# Enable and start agents
sudo systemctl daemon-reload
sudo systemctl enable --now agentforge-executor@xunyu
sudo systemctl enable --now agentforge-ws@xunyu
sudo systemctl enable --now agentforge-scheduler
```

## Agent Roles

| Agent ID | Name | Role | Expertise |
|----------|------|------|-----------|
| `zhugeliang` | 诸葛亮 | Architect | Architecture design, code review, technical standards |
| `liubei` | 刘备 | Project Manager | Summary, progress, management, allocation, coordination |
| `guanyu` | 关羽 | Backend Dev | Java, API, services, Spring, database operations |
| `zhaoyun` | 赵云 | Frontend Dev | Vue, React, pages, styles, components, UI |
| `xunyu` | 荀彧 | DBA | Database, SQL, tables, indexes, performance optimization |
| `zhangfei` | 张飞 | QA / Tester | Testing, bugs, defect verification, regression |
| `huatuo` | 华佗 | Product Manager | Product, requirements, user experience, PRD |
| `chenlin` | 陈琳 | Tech Writer | Documentation, manuals, wiki, release notes |

## Core Modules

| Module | Description |
|--------|-------------|
| `agentforge/enhanced_executor.py` | Core agent executor: LLM calls, tool execution, message routing, self-optimization |
| `agentforge/ws_listener.py` | Global WebSocket listener (single dispatcher for all agents) |
| `agentforge/ws_listener_instance.py` | Per-agent WebSocket listener instance |
| `agentforge/self_optimizer.py` | Auto-reflection, dynamic rule updates, model performance tracking |
| `agentforge/experience_memory.py` | Persistent experience storage (success/failure/lessons) |
| `agentforge/scheduler.py` | Cron-style scheduler for periodic tasks |
| `agentforge/workflow_engine.py` | Multi-step workflow engine (serial/parallel/approval) |
| `agentforge/skill_registry.py` | Skill discovery, installation, and recommendation |
| `agentforge/config.py` | Configuration loader (env vars + JSON files) |

## Message Flow

1. User sends message in Feishu group chat or DM
2. WS listener parses message, determines target agent via @mention or keyword scoring
3. Message pushed to Redis Stream (`agent-work-queue`)
4. Agent executor picks up the task
5. If broadcast, intent routing decides whether this agent should respond
6. Tool execution runs first (scripts for zentao, git, etc.)
7. Tool results (if `__RAW__`) bypass LLM; otherwise LLM generates response
8. Response sent back to Feishu (group or DM based on context)
9. Post-task reflection runs asynchronously (self-optimization)

## Anti-Hallucination Strategy

The framework prevents LLM hallucination through multiple layers:

1. **`__RAW__` marker** - Certain tool outputs (bug summaries, progress reports) are returned directly without LLM processing
2. **System prompt injection** - Tool results are injected into the system prompt with strict instructions: "必须基于此回复"
3. **SOUL.md constraints** - Each agent's personality file includes strict anti-hallucination rules
4. **Dynamic rules** - Self-optimization adds learned constraints to the system prompt over time

## Directory Structure

```
agentforge/
├── agentforge/              # Python package
│   ├── __init__.py
│   ├── config.py            # Configuration loader
│   ├── enhanced_executor.py # Main executor
│   ├── self_optimizer.py    # Self-optimization
│   ├── experience_memory.py # Experience storage
│   ├── ws_listener.py       # Global WS listener
│   ├── ws_listener_instance.py  # Per-agent WS listener
│   ├── scheduler.py         # Cron scheduler
│   ├── workflow_engine.py   # Workflow engine
│   └── skill_registry.py    # Skill registry
├── config/
│   ├── feishu_credentials.json.example
│   └── agents/
│       └── example-agent/
│           └── SOUL.md.example
├── skills/
│   ├── builtin/
│   ├── community/
│   └── custom/
├── scripts/                  # External tool scripts
├── systemd/                  # Service templates
│   ├── agentforge-executor@.service
│   ├── agentforge-ws@.service
│   └── agentforge-scheduler.service
├── .env.example
├── .gitignore
├── requirements.txt
├── LICENSE
└── README.md
```

## Adding a New Agent

1. Create config directory: `mkdir -p config/agents/new-agent`
2. Write personality: `vim config/agents/new-agent/SOUL.md`
3. Add Feishu credentials to `config/feishu_credentials.json`
4. Start the executor: `python3 -m agentforge.enhanced_executor --agent new-agent`
5. (Optional) Add expertise keywords in `enhanced_executor.py` for intent routing

## License

Apache License 2.0
