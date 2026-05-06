"""AgentForge - Multi-Agent Collaboration Framework"""

__version__ = "1.0.0"

# Auto-load .env as early as possible
import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    # Try: project root (editable mode) then cwd
    for env_path in [Path(__file__).parents[1] / ".env", Path.cwd() / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass
