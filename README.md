# AgentForge

> Multi-agent collaboration framework with Feishu integration, tool execution, autonomous optimization, and workflow orchestration.

AgentForge deploys multiple AI agents (each with a distinct role) that collaborate through a Redis message queue to handle real-world tasks — bug tracking, code management, project reporting, and more. Each agent connects to Feishu (Lark) for human interaction, calls LLM APIs for reasoning, and executes external scripts for real-world actions.

## Architecture

```
┌─────────────── Feishu (Lark) ───────────────┐
│         Group Chat / Direct Messages         │
└───────────────┬─────────────────────────────┘
                │ WebSocket (per-agent)
                ▼
┌─── network/ws_listener.py ──────────────────┐
│  Parse @mention + keyword routing            │
│  Persistent Redis connection (reused)        │
└───────────────┬─────────────────────────────┘
                │ Redis Stream (agent-work-queue)
                ▼
┌─── core/executor.py (AgentExecutor) ────────┐
│  ┌────────────┐ ┌───────────┐ ┌──────────┐  │
│  │ Intent     │ │Tool Exec  │ │ LLM      │  │
│  │ Routing    │ │(safe subs │ │ Client   │  │
│  │            │ │ process)  │ │ w/retry  │  │
│  └────────────┘ └───────────┘ └──────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │  Self-Optimizer + Experience Memory     │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │  Pipeline: Fix → Test → Verify → Done   │  │
│  │  (Claude Code autonomous fix included)  │  │
│  └────────────────────────────────────────┘  │
└───────────────┬─────────────────────────────┘
                │
     ┌──────────┼──────────┐
     ▼          ▼          ▼
  Feishu    Redis Stream  External Scripts
  Reply     (cross-agent) (zentao, git, claude-code)
```

## Features

- **Multi-Agent Roles** — 8 preset personas with independent SOUL.md definitions
- **Anti-Hallucination** — Tool results marked `__RAW__` bypass the LLM entirely
- **Model Routing** — Auto-selects best model per task type (coding / analysis / simple)
- **Autonomous Pipeline** — Boot self-check → bug query → Claude Code fix → commit → test → verify
- **ZenTao Write Ops** — Bug assign back to reporter, resolve by PM
- **Self-Optimization** — Post-task reflection, experience memory, dynamic rule updates
- **Intent-Based Routing** — Keyword scoring determines which agent responds
- **Workflow Engine** — Serial, parallel, and approval-gated multi-step workflows
- **Skill Registry** — Discoverable, installable skills with keyword matching
- **Hermes Brain** — Optional integration with Hermes Agent for persistent memory and tool calling

### Security & Reliability (v1.1+)

- **Safe subprocess** — User data passed as argv, never interpolated into shell
- **Unified Config** — Single `Config` dataclass, no scattered `os.environ.get()`
- **Structured Logging** — `logging` module with timestamps and levels
- **LLM Retry** — Exponential backoff (3 attempts) on network failure
- **Thread-safe Memory** — `fcntl` file locking prevents concurrent write corruption
- **Persistent Redis** — WS listener reuses a single connection instead of creating per-message
- **Direct Feishu API** — `requests.post` instead of `curl` + tempfile

## Quick Start

### Prerequisites

- Python 3.10+
- Redis
- Feishu (Lark) open platform apps
- Dashscope / Bailian API (or any OpenAI-compatible API)

### Setup

```bash
git clone https://github.com/paskaa/agentforge.git
cd agentforge
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit with your values
cp config/feishu_credentials.json.example config/feishu_credentials.json
```

### Run

```bash
# Agent executor
python3 -m agentforge executor --agent xunyu

# WebSocket listener
python3 -m agentforge ws-listener --agent xunyu

# Scheduler (daily reports + health checks)
python3 -m agentforge scheduler

# Workflow engine
python3 -m agentforge workflow list
python3 -m agentforge workflow create

# Skills
python3 -m agentforge skills list
python3 -m agentforge skills find "查询 bug"
```

### Systemd Deployment

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentforge-executor@xunyu
sudo systemctl enable --now agentforge-ws@xunyu
sudo systemctl enable --now agentforge-scheduler
```

## Directory Structure

```
agentforge/
├── src/agentforge/             # Python package
│   ├── __init__.py             # Auto-loads .env
│   ├── __main__.py
│   ├── cli.py                  # CLI entry point + logging setup
│   ├── config.py               # Unified Config dataclass (single source of truth)
│   ├── core/                   # Core modules
│   │   ├── executor.py         # AgentExecutor — main loop, routing, pipeline
│   │   ├── llm.py              # LLMClient (retry) + SessionManager
│   │   ├── tool_executor.py    # Safe subprocess runner (no shell injection)
│   │   ├── optimizer.py        # Self-optimization & reflection
│   │   └── memory.py           # Thread-safe experience memory (fcntl)
│   ├── network/                # Network / messaging
│   │   ├── feishu.py           # Feishu API wrapper (requests, not curl)
│   │   └── ws_listener.py      # WebSocket listener (persistent Redis)
│   ├── tools/                  # Tools & utilities
│   │   ├── skill_registry.py   # Skill discovery & install
│   │   └── scheduler.py        # Cron-style scheduler (Config-aware)
│   ├── workflow/               # Workflow orchestration
│   │   └── engine.py           # Multi-step workflow engine
│   └── hermes_bridge.py        # Hermes Agent integration (optional)
├── config/
│   ├── agents/                 # Per-agent SOUL.md + experience
│   │   └── example/
│   │       └── SOUL.md.example
│   ├── gateway/                # Per-agent LLM gateway config (optional)
│   └── feishu_credentials.json.example
├── scripts/                    # External tool scripts (zentao, git, etc.)
├── skills/
│   ├── builtin/                # Built-in skills
│   ├── community/              # Community skills
│   └── custom/                 # Custom installed skills
├── deploy/
│   └── systemd/                # systemd service templates
├── .env                        # Real config (git-ignored)
├── .env.example                # Template (committed)
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── LICENSE
└── README.md
```

## Agent Roles

| ID | Name | Role | Expertise |
|---|---|---|---|
| `zhugeliang` | 诸葛亮 | Architect | Architecture, code review, standards |
| `liubei` | 刘备 | PM | Summary, progress, management |
| `guanyu` | 关羽 | Backend | Java, API, Spring, services |
| `zhaoyun` | 赵云 | Frontend | Vue, React, UI, components |
| `xunyu` | 荀彧 | DBA | SQL, database, performance |
| `zhangfei` | 张飞 | QA | Testing, bugs, zen-tao, regression |
| `huatuo` | 华佗 | Product | Requirements, UX, PRD |
| `chenlin` | 陈琳 | Tech Writer | Docs, wiki, release notes |

## Configuration

All sensitive info lives in `.env` and `config/feishu_credentials.json` — **neither is committed to git**.

### `.env`

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
FEISHU_GROUP_CHAT_ID=oc_xxxx
FEISHU_CREDENTIALS_FILE=./config/feishu_credentials.json
BAILIAN_API_KEY=sk-xxx
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_DEFAULT_MODEL=qwen-plus
MODEL_CODING=qwen-coder-plus
MODEL_ANALYSIS=qwen-plus
MODEL_SIMPLE=qwen-turbo

# Optional: Hermes brain integration
# HERMES_ENABLED=1
# HERMES_HOME=/root/hermes-agent

# Optional: Zentao scripts (defaults to production path)
# ZENTAO_SCRIPTS_DIR=/root/.openclaw/extensions/zentao-token-refresh

SCRIPTS_DIR=./scripts
AGENTS_CONFIG_DIR=./config/agents
```

### `config/agents/{id}/SOUL.md`

Each agent needs a SOUL.md with role definition and anti-hallucination rules. See `config/agents/example/SOUL.md.example`.

### `config/gateway/{id}.json` (optional)

Per-agent LLM gateway config. If present, overrides `.env` API key/base URL for that agent.

## License

Apache-2.0
