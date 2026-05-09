"""
LLM Client — OpenAI-compatible API with retry logic.

Provides clean call_llm() with exponential backoff,
model selection, and session history management.
"""

import json
import time
import logging
import requests
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentforge.llm")


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions with retry."""

    def __init__(self, api_key: str, api_base: str, model: str,
                 max_retries: int = 3, retry_delay: float = 1.0):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Model routing table
        self.model_routes: dict[str, str] = {}

    def select_model(self, task_type: str = "default") -> str:
        return self.model_routes.get(task_type, self.model_routes.get("default", self.model))

    def call(self, messages: list[dict], model: Optional[str] = None,
             max_tokens: int = 2000, temperature: float = 0.7,
             timeout: int = 180) -> Optional[str]:
        """Call LLM with retry. Returns response text or None on failure."""
        model = model or self.model
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                data = resp.json()
                if data.get("choices"):
                    return data["choices"][0]["message"]["content"]
                last_error = f"API returned: {data}"
            except requests.Timeout:
                last_error = "timeout"
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                delay = self.retry_delay * (2 ** (attempt - 1))
                logger.warning("LLM call attempt %d failed (%s), retrying in %.1fs",
                               attempt, last_error, delay)
                time.sleep(delay)

        logger.error("LLM call failed after %d attempts: %s", self.max_retries, last_error)
        return None


class SessionManager:
    """Manages per-conversation session history on disk."""

    def __init__(self, session_dir: Path, max_history: int = 10):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.max_history = max_history

    def _path(self, conversation_id: str) -> Path:
        return self.session_dir / f"{conversation_id or 'default'}.json"

    def load(self, conversation_id: str) -> list[dict]:
        path = self._path(conversation_id)
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save(self, conversation_id: str, history: list[dict]):
        path = self._path(conversation_id)
        with open(path, "w") as f:
            json.dump(history[-self.max_history:], f, ensure_ascii=False, indent=2)

    def append(self, conversation_id: str, user_msg: str, assistant_msg: str):
        history = self.load(conversation_id)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})
        self.save(conversation_id, history)
