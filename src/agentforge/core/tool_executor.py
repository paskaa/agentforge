"""
Safe Script Runner — subprocess wrapper with input sanitization.

All external scripts are called through this module;
user-supplied data is never interpolated directly into shell commands.
"""

import logging
import shlex
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentforge.tools")


def run_script(script_path: str | Path, *args: str,
               timeout: int = 30, cwd: Optional[str] = None,
               extra_env: dict = None) -> tuple[int, str, str]:
    """
    Run a shell script safely.

    Args:
        script_path: Absolute or relative path to the script.
        *args: Positional arguments passed to the script.
               These are NOT interpreted by a shell — they are passed
               directly as argv to bash, preventing command injection.
        timeout: Max execution time in seconds.
        cwd: Optional working directory.

    Returns:
        (returncode, stdout_stripped, stderr_stripped)
    """
    script = Path(script_path)
    if not script.is_absolute():
        # Resolve relative to a sensible base — caller should pass absolute
        script = script.resolve()

    full_args = ["bash", str(script)] + [str(a) for a in args]

    logger.debug("Running: %s", shlex.join(full_args))

    try:
        env = None
        if extra_env:
            import os as _os
            env = {**_os.environ, **extra_env}

        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.warning("Script timed out after %ds: %s", timeout, script.name)
        return -1, "", "timeout"
    except Exception as e:
        logger.error("Script failed: %s — %s", script.name, e)
        return -1, "", str(e)


def run_safe(script_path: str | Path, timeout: int = 30,
             cwd: Optional[str] = None) -> tuple[int, str, str]:
    """Run a script with no arguments (simplest form)."""
    return run_script(script_path, timeout=timeout, cwd=cwd)
