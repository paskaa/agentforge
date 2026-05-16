"""
API Quota Monitor — detect 429 rate limits and auto-recover.

Runs as a background thread in each executor. When a 429 is hit:
  1. Marks the agent as "rate_limited"
  2. Falls back to direct LLM (non-Hermes)
  3. Background thread probes API every 60s
  4. When API recovers, automatically re-enables Hermes
"""

import logging
import os
import subprocess
import threading
import time

logger = logging.getLogger("agentforge.quota_monitor")

# Check interval in seconds
PROBE_INTERVAL = 60       # Check every 60s (matches 1min quota release)
COOLDOWN_SECONDS = 60     # Bailian releases quota every minute on rolling basis
MAX_CONSECUTIVE_429 = 30  # After 30 consecutive 429s (30 min), do permanent fallback

# Agent-level state
_rate_limited: dict[str, float] = {}  # agent_id -> last 429 timestamp
_consecutive_429: dict[str, int] = {}  # agent_id -> consecutive 429 count
_lock = threading.Lock()


def mark_rate_limited(agent_id: str) -> None:
    """Call when an agent hits a 429 error. Uses cooldown, not permanent disable."""
    with _lock:
        was_limited = agent_id in _rate_limited
        _rate_limited[agent_id] = time.time()
        _consecutive_429[agent_id] = _consecutive_429.get(agent_id, 0) + 1
        cons = _consecutive_429[agent_id]
    
    if cons >= MAX_CONSECUTIVE_429:
        if not was_limited:
            logger.warning("[quota] Agent %s: %d consecutive 429s, permanent fallback to direct LLM", agent_id, cons)
            _start_recovery_probe(agent_id)
    elif not was_limited:
        logger.info("[quota] Agent %s QPM limited, will retry after cooldown (%ds)", agent_id, COOLDOWN_SECONDS)


def is_rate_limited(agent_id: str) -> bool:
    """Check if agent is in cooldown from a recent 429."""
    with _lock:
        cons = _consecutive_429.get(agent_id, 0)
        if cons >= MAX_CONSECUTIVE_429:
            return True  # Permanently limited until probe recovers
        last = _rate_limited.get(agent_id, 0)
        if last and time.time() - last < COOLDOWN_SECONDS:
            return True  # Still in cooldown
        if last and time.time() - last >= COOLDOWN_SECONDS:
            # Cooldown expired, reset consecutive count
            _consecutive_429[agent_id] = 0
            _rate_limited.pop(agent_id, None)
            logger.info("[quota] Agent %s cooldown expired, retrying", agent_id)
        return False


def _probe_api(agent_id: str) -> bool:
    """Probe the API with a minimal call. Returns True if recovered."""
    import requests
    import json
    
    api_key = os.environ.get("BAILIAN_API_KEY", "")
    if not api_key:
        return True  # Can't probe, assume recovered
    
    # Try DeepSeek first (free tier, separate quota)
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if ds_key:
        try:
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
    
    # Fall back to Bailian probe
    try:
        resp = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "qwen-turbo",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 429:
            return False
        return True
    except Exception:
        return True


def _start_recovery_probe(agent_id: str) -> None:
    """Start a background thread that probes API until recovery."""
    def _probe_loop():
        attempt = 0
        max_attempts = 60  # 60 probes × 60s = 1 hour max
        while attempt < max_attempts:
            time.sleep(PROBE_INTERVAL)
            attempt += 1
            try:
                if _probe_api(agent_id):
                    logger.info("[quota] Agent %s API recovered after %d probes", agent_id, attempt)
                    with _lock:
                        _rate_limited.pop(agent_id, None)
                        _consecutive_429.pop(agent_id, None)
                    return
            except Exception as e:
                logger.debug("[quota] Probe error for %s: %s", agent_id, e)
            logger.debug("[quota] Agent %s still limited (probe %d/%d)", agent_id, attempt, max_attempts)
        
        logger.warning("[quota] Agent %s recovery probe exhausted (%d attempts), resetting state", agent_id, max_attempts)
        with _lock:
            _rate_limited.pop(agent_id, None)
            _consecutive_429.pop(agent_id, None)

    t = threading.Thread(target=_probe_loop, daemon=True)
    t.start()
    logger.info("[quota] Started recovery probe for %s (every %ds, max %d probes)", agent_id, PROBE_INTERVAL, 60)
