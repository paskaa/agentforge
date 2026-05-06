"""
AgentForge Configuration Loader

Loads from:
1. .env file (auto-detected)
2. Environment variables
3. JSON config files
"""

import json
import os
from pathlib import Path

# Auto-load .env
try:
    from dotenv import load_dotenv
    for env_path in [Path(__file__).parents[2] / ".env", Path.cwd() / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass


class Config:
    """Global configuration"""

    def __init__(self, base_dir=None):
        self.base_dir = Path(base_dir or os.environ.get("AGENTFORGE_BASE_DIR", "."))

        # Redis
        self.redis_host = os.environ.get("REDIS_HOST", "127.0.0.1")
        self.redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        self.redis_db = int(os.environ.get("REDIS_DB", "0"))
        self.redis_password = os.environ.get("REDIS_PASSWORD", "")

        # Feishu
        self.feishu_group_chat_id = os.environ.get("FEISHU_GROUP_CHAT_ID", "")
        self.feishu_credentials_file = os.environ.get(
            "FEISHU_CREDENTIALS_FILE",
            str(self.base_dir / "config" / "feishu_credentials.json"),
        )

        # LLM
        self.bailian_api_key = os.environ.get("BAILIAN_API_KEY", "")
        self.bailian_base_url = os.environ.get(
            "BAILIAN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.bailian_default_model = os.environ.get("BAILIAN_DEFAULT_MODEL", "qwen-plus")

        # Model routing
        self.model_routing = {
            "coding": os.environ.get("MODEL_CODING", "qwen-coder-plus"),
            "analysis": os.environ.get("MODEL_ANALYSIS", "qwen-plus"),
            "simple": os.environ.get("MODEL_SIMPLE", "qwen-turbo"),
        }

        # Paths
        self.scripts_dir = Path(os.environ.get("SCRIPTS_DIR", str(self.base_dir / "scripts")))
        self.skills_dir = Path(os.environ.get("SKILLS_DIR", str(self.base_dir / "skills")))
        self.agents_config_dir = Path(os.environ.get("AGENTS_CONFIG_DIR", str(self.base_dir / "config" / "agents")))

    def load_feishu_credentials(self):
        with open(self.feishu_credentials_file) as f:
            return json.load(f)

    def load_agent_soul(self, agent_id):
        soul_path = self.agents_config_dir / agent_id / "agent" / "SOUL.md"
        with open(soul_path) as f:
            return f.read()

    def load_gateway_config(self, agent_id):
        gw_path = self.base_dir / "config" / "gateway" / f"{agent_id}.json"
        if gw_path.exists():
            with open(gw_path) as f:
                return json.load(f)
        return {
            "models": {
                "providers": {
                    "bailian": {
                        "apiKey": self.bailian_api_key,
                        "baseUrl": self.bailian_base_url,
                    }
                }
            }
        }

    def get_agent_experience_path(self, agent_id):
        return str(self.agents_config_dir / agent_id / "agent" / ".experience.json")

    def get_agent_dynamic_rules_path(self, agent_id):
        return str(self.agents_config_dir / agent_id / "agent" / ".dynamic_rules.md")
