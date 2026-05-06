"""
AgentForge Configuration Loader

Loads configuration from:
1. Environment variables
2. .env file (if python-dotenv available)
3. JSON config files
"""

import json
import os
from pathlib import Path

# Auto-load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    # Try project root .env first, then current directory
    for env_path in [Path(__file__).parent.parent / ".env", Path(".") / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass


class Config:
    """Global configuration loader"""

    def __init__(self, base_dir=None):
        self.base_dir = Path(base_dir or os.environ.get("AGENTFORGE_BASE_DIR", "."))

        # Redis
        self.redis_host = os.environ.get("REDIS_HOST", "127.0.0.1")
        self.redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        self.redis_db = int(os.environ.get("REDIS_DB", "0"))
        self.redis_password = os.environ.get("REDIS_PASSWORD", "")

        # Feishu
        self.feishu_group_chat_id = os.environ.get(
            "FEISHU_GROUP_CHAT_ID", ""
        )
        self.feishu_credentials_file = os.environ.get(
            "FEISHU_CREDENTIALS_FILE",
            str(self.base_dir / "feishu_credentials.json"),
        )

        # LLM (Bailian / Dashscope)
        self.bailian_api_key = os.environ.get("BAILIAN_API_KEY", "")
        self.bailian_base_url = os.environ.get(
            "BAILIAN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.bailian_default_model = os.environ.get(
            "BAILIAN_DEFAULT_MODEL", "qwen-plus"
        )

        # Model routing
        self.model_routing = {
            "coding": os.environ.get("MODEL_CODING", "qwen-coder-plus"),
            "analysis": os.environ.get("MODEL_ANALYSIS", "qwen-plus"),
            "simple": os.environ.get("MODEL_SIMPLE", "qwen-turbo"),
        }

        # Paths
        self.scripts_dir = Path(
            os.environ.get("SCRIPTS_DIR", str(self.base_dir / "scripts"))
        )
        self.skills_dir = Path(
            os.environ.get("SKILLS_DIR", str(self.base_dir / "skills"))
        )
        self.agents_config_dir = Path(
            os.environ.get(
                "AGENTS_CONFIG_DIR",
                str(self.base_dir / "agents"),
            )
        )

    def load_feishu_credentials(self):
        """Load Feishu app credentials from JSON file"""
        with open(self.feishu_credentials_file) as f:
            return json.load(f)

    def load_agent_soul(self, agent_id):
        """Load SOUL.md for an agent"""
        soul_path = self.agents_config_dir / agent_id / "SOUL.md"
        with open(soul_path) as f:
            return f.read()

    def load_gateway_config(self, agent_id):
        """Load LLM gateway config for an agent"""
        gw_path = self.base_dir / "gateway" / f"{agent_id}.json"
        if gw_path.exists():
            with open(gw_path) as f:
                return json.load(f)
        # Fallback to env defaults
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
        """Get experience file path for an agent"""
        return str(
            self.agents_config_dir / agent_id / ".experience.json"
        )

    def get_agent_dynamic_rules_path(self, agent_id):
        """Get dynamic rules file path for an agent"""
        return str(
            self.agents_config_dir / agent_id / ".dynamic_rules.md"
        )
