"""
AgentForge Configuration Loader — single source of truth.

Loads from:
  1. .env file (auto-detected at project root or cwd)
  2. Environment variables
  3. JSON config files (feishu credentials, gateway, openclaw models)

All modules receive a Config instance; no more scattered os.environ.get().
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field


def _env(key: str, fallback: str) -> str:
    """Get env var, stripping inline comments (# ...) that python-dotenv leaves in."""
    val = os.environ.get(key, fallback)
    return val.split("#")[0].strip()

# Auto-load .env as early as possible
try:
    from dotenv import load_dotenv
    for _env_path in [Path(__file__).parents[2] / ".env", Path.cwd() / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path)
            break
except ImportError:
    pass


def _env_dir(key: str, fallback: str) -> Path:
    return Path(os.environ.get(key, fallback)).resolve()


@dataclass
class Config:
    """Global configuration for AgentForge."""

    # --- Base paths ---
    base_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "AGENTFORGE_BASE_DIR", str(Path(__file__).parents[2])
    )).resolve())

    # --- Redis ---
    redis_host: str = field(default_factory=lambda: os.environ.get("REDIS_HOST", "127.0.0.1"))
    redis_port: int = field(default_factory=lambda: int(os.environ.get("REDIS_PORT", "6379")))
    redis_db: int = field(default_factory=lambda: int(os.environ.get("REDIS_DB", "0")))
    redis_password: str = field(default_factory=lambda: os.environ.get("REDIS_PASSWORD", ""))
    redis_stream: str = "agent-work-queue"

    # --- Feishu ---
    feishu_group_chat_id: str = field(default_factory=lambda: os.environ.get(
        "FEISHU_GROUP_CHAT_ID", ""
    ))
    feishu_credentials_file: Path = field(default_factory=lambda: Path(os.environ.get(
        "FEISHU_CREDENTIALS_FILE",
        str(Path(__file__).parents[2] / "config" / "feishu_credentials.json"),
    )))

    # --- LLM / Bailian ---
    bailian_api_key: str = field(default_factory=lambda: os.environ.get("BAILIAN_API_KEY", ""))
    bailian_base_url: str = field(default_factory=lambda: os.environ.get(
        "BAILIAN_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ))
    bailian_default_model: str = field(default_factory=lambda: os.environ.get(
        "BAILIAN_DEFAULT_MODEL", "qwen-plus"
    ))

    # Model routing
    model_routing: dict = field(default_factory=lambda: {
        "coding": _env("MODEL_CODING", "qwen-coder-plus"),
        "analysis": _env("MODEL_ANALYSIS", "qwen-plus"),
        "simple": _env("MODEL_SIMPLE", "qwen-turbo"),
        "eval": _env("MODEL_EVAL", "qwen-turbo"),
    })

    # --- OpenClaw model config (optional, for richer model list) ---
    openclaw_config_file: Path = field(default_factory=lambda: Path(os.environ.get(
        "OPENCLAW_CONFIG_FILE", "/root/.openclaw/openclaw.json"
    )))

    # --- Paths ---
    scripts_dir: Path = field(default_factory=lambda: _env_dir(
        "SCRIPTS_DIR", str(Path(__file__).parents[2] / "scripts")
    ))
    skills_dir: Path = field(default_factory=lambda: _env_dir(
        "SKILLS_DIR", str(Path(__file__).parents[2] / "skills")
    ))
    agents_config_dir: Path = field(default_factory=lambda: _env_dir(
        "AGENTS_CONFIG_DIR", str(Path(__file__).parents[2] / "config" / "agents")
    ))
    gateway_dir: Path = field(default_factory=lambda: _env_dir(
        "GATEWAY_DIR", str(Path(__file__).parents[2] / "config" / "gateway")
    ))

    # --- Zentao / external tools (from running version) ---
    zentao_scripts_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "ZENTAO_SCRIPTS_DIR", "/root/.openclaw/extensions/zentao-token-refresh"
    )))

    # --- Agent account mapping for Zentao ---
    agent_accounts: dict = field(default_factory=lambda: {
        "zhugeliang": "zhugeliang", "liubei": "liubei", "guanyu": "guanyu",
        "zhaoyun": "zhaoyun", "xunyu": "xunyu", "zhangfei": "zhangfei",
        "huatuo": "huatuo", "chenlin": "chenlin",
    })

    # --- Agent name mapping ---
    agent_names: dict = field(default_factory=lambda: {
        "zhugeliang": "诸葛亮", "liubei": "刘备", "guanyu": "关羽", "zhaoyun": "赵云",
        "xunyu": "荀彧", "zhangfei": "张飞", "huatuo": "华佗", "chenlin": "陈琳",
    })

    # --- Expertise keywords for intent routing ---
    expertise: dict = field(default_factory=lambda: {
        "zhugeliang": ["架构", "设计", "方案", "review", "技术评审", "重构", "规范", "标准", "api设计"],
        "liubei": ["汇总", "项目", "进度", "管理", "分配", "协调", "报告", "统计", "概览", "项目经理", "所有"],
        "guanyu": ["后端", "java", "api", "接口", "服务", "数据库操作", "spring", "service", "controller", "mapper"],
        "zhaoyun": ["前端", "vue", "react", "页面", "样式", "css", "组件", "表单", "按钮", "ui", "交互"],
        "xunyu": ["数据库", "sql", "表", "查询", "索引", "性能", "慢查询", "优化", "数据", "mysql", "备份"],
        "zhangfei": ["测试", "bug", "缺陷", "验证", "复现", "禅道", "用例", "回归", "验收", "qa"],
        "huatuo": ["产品", "需求", "功能", "用户", "体验", "prd", "业务流程", "临床", "his", "门诊", "住院"],
        "chenlin": ["文档", "说明", "手册", "wiki", "知识库", "培训", "发布", "变更", "公告"],
    })

    # --- Hermes ---
    hermes_enabled: bool = field(default_factory=lambda: os.environ.get("HERMES_ENABLED", "0") == "1")
    hermes_home: Path = field(default_factory=lambda: Path(os.environ.get(
        "HERMES_HOME", "/root/hermes-agent"
    )))

    # --- Sessions ---
    session_base_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "SESSION_DIR", "/tmp/agentforge-sessions"
    )))

    # =========================================================================
    #  Derived helpers
    # =========================================================================

    def load_feishu_credentials(self) -> dict:
        with open(self.feishu_credentials_file) as f:
            return json.load(f)

    def get_feishu_app(self, agent_id: str) -> dict:
        creds = self.load_feishu_credentials()
        return creds["agents"].get(agent_id, {})

    def load_agent_soul(self, agent_id: str) -> str:
        soul_path = self.agents_config_dir / agent_id / "agent" / "SOUL.md"
        with open(soul_path) as f:
            return f.read()

    def load_gateway_config(self, agent_id: str) -> dict:
        gw_path = self.gateway_dir / f"{agent_id}.json"
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

    def get_llm_config(self, agent_id: str) -> dict:
        """Return (api_key, api_base, model) for an agent, preferring gateway config."""
        gw = self.load_gateway_config(agent_id)
        providers = gw.get("models", {}).get("providers", {})
        bailian = providers.get("bailian", {})
        return {
            "api_key": bailian.get("apiKey", self.bailian_api_key),
            "api_base": bailian.get("baseUrl", self.bailian_base_url),
            "model": bailian.get("model", self.bailian_default_model),
        }

    def load_openclaw_models(self) -> list:
        """Load available models from openclaw.json (if present)."""
        if self.openclaw_config_file.exists():
            try:
                with open(self.openclaw_config_file) as f:
                    cfg = json.load(f)
                providers = cfg.get("models", {}).get("providers", {})
                bailian = providers.get("bailian", {})
                return bailian.get("models", [])
            except Exception:
                pass
        return []

    def get_agent_experience_path(self, agent_id: str) -> str:
        return str(self.agents_config_dir / agent_id / "agent" / ".experience.json")

    def get_agent_dynamic_rules_path(self, agent_id: str) -> str:
        return str(self.agents_config_dir / agent_id / "agent" / ".dynamic_rules.md")

    def get_agent_name(self, agent_id: str) -> str:
        return self.agent_names.get(agent_id, agent_id)

    def get_agent_account(self, agent_id: str) -> str:
        return self.agent_accounts.get(agent_id, agent_id)

    def get_session_dir(self, agent_id: str) -> Path:
        d = self.session_base_dir / agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def redis_kwargs(self) -> dict:
        kw = {
            "host": self.redis_host,
            "port": self.redis_port,
            "db": self.redis_db,
            "decode_responses": True,
        }
        if self.redis_password:
            kw["password"] = self.redis_password
        return kw
