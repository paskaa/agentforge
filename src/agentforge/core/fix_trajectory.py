"""
Fix Trajectory Store — captures full fix reasoning chains for analysis.

Every bug fix attempt (Claude Code, LLMFixer) saves its complete
output, search results, and generated code to a dedicated directory.
This enables post-mortem analysis of WHY a fix succeeded or failed.

Directory: /var/lib/agentforge/trajectories/{bug_id}/
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentforge.trajectory")

TRAJECTORY_DIR = Path("/var/lib/agentforge/trajectories")
TRAJECTORY_DIR.mkdir(parents=True, exist_ok=True)


def save_trajectory(bug_id: str, agent_name: str, method: str,
                    success: bool, elapsed: float,
                    stdout: str = "", stderr: str = "",
                    files_searched: list = None,
                    generated_fix: str = "",
                    fix_summary: str = "") -> Path:
    """
    Save a complete fix trajectory for later analysis.

    Args:
        bug_id: Bug number
        agent_name: Which agent attempted the fix
        method: 'claude_code' | 'llm_fixer' | 'manual'
        success: Whether the fix was applied
        elapsed: Time taken in seconds
        stdout: Full stdout from the fix tool
        stderr: Full stderr from the fix tool
        files_searched: List of files examined
        generated_fix: The full generated fix code (LLMFixer only)
        fix_summary: One-line summary of what happened

    Returns: Path to the saved trajectory directory
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bug_dir = TRAJECTORY_DIR / f"bug{bug_id}"
    run_dir = bug_dir / f"{ts}_{method}_{'OK' if success else 'FAIL'}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    meta = {
        "bug_id": bug_id,
        "agent": agent_name,
        "method": method,
        "success": success,
        "elapsed_s": round(elapsed, 1),
        "timestamp": datetime.now().isoformat(),
        "files_searched": files_searched or [],
        "fix_summary": fix_summary,
    }
    with open(run_dir / "meta.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # Save full stdout
    if stdout:
        with open(run_dir / "stdout.txt", "w") as f:
            f.write(stdout)

    # Save full stderr
    if stderr:
        with open(run_dir / "stderr.txt", "w") as f:
            f.write(stderr)

    # Save generated fix code (LLMFixer)
    if generated_fix:
        with open(run_dir / "generated_fix.txt", "w") as f:
            f.write(generated_fix)

    # Update index
    _update_index(bug_id, meta)

    logger.info("[trajectory] Saved Bug #%s %s %s → %s",
                bug_id, method, "OK" if success else "FAIL", run_dir.name)
    return run_dir


def get_trajectories(bug_id: str, limit: int = 10) -> list[dict]:
    """Get all fix trajectories for a bug, newest first."""
    bug_dir = TRAJECTORY_DIR / f"bug{bug_id}"
    if not bug_dir.exists():
        return []

    runs = sorted(bug_dir.iterdir(), key=lambda p: p.name, reverse=True)
    results = []
    for run in runs[:limit]:
        meta_file = run / "meta.json"
        if not meta_file.exists():
            continue
        try:
            with open(meta_file) as f:
                meta = json.load(f)
            meta["trajectory_dir"] = str(run)
            results.append(meta)
        except Exception:
            pass
    return results


def get_latest_failure(bug_id: str) -> Optional[dict]:
    """Get the most recent failed trajectory for a bug."""
    trajectories = get_trajectories(bug_id)
    for t in trajectories:
        if not t.get("success", True):
            return t
    return None


def get_failure_analysis(bug_id: str) -> str:
    """Generate a summary of why the last fix attempt failed."""
    traj = get_latest_failure(bug_id)
    if not traj:
        return ""

    analysis = [f"## Bug #{bug_id} 最近修复失败分析"]
    analysis.append(f"  方法: {traj.get('method')}")
    analysis.append(f"  Agent: {traj.get('agent')}")
    analysis.append(f"  耗时: {traj.get('elapsed_s', 0):.0f}s")
    analysis.append(f"  搜索文件: {', '.join(traj.get('files_searched', [])[:5])}")

    # Try to read stdout for clues
    traj_dir = Path(traj.get("trajectory_dir", ""))
    stdout_file = traj_dir / "stdout.txt"
    if stdout_file.exists():
        with open(stdout_file) as f:
            stdout = f.read()
        # Look for error indicators
        if "already been fixed" in stdout.lower() or "已在历史记录中修复" in stdout:
            analysis.append("  原因: Bug 已在 git 历史中修复，Claude Code 跳过")
        elif "429" in stdout:
            analysis.append("  原因: API 配额不足 (429)")
        elif "model" in stdout.lower() and "not supported" in stdout.lower():
            analysis.append("  原因: 模型不支持")
        elif "Search block not found" in stdout:
            analysis.append("  原因: 生成的代码块与源文件不匹配")
        else:
            # Show last meaningful line
            lines = [l.strip() for l in stdout.split("\n") if l.strip()]
            if lines:
                analysis.append(f"  最后输出: {lines[-1][:200]}")

    return "\n".join(analysis)


def _update_index(bug_id: str, meta: dict):
    """Maintain a rolling index of all trajectories."""
    index_file = TRAJECTORY_DIR / "index.json"
    index = []
    if index_file.exists():
        try:
            with open(index_file) as f:
                index = json.load(f)
        except Exception:
            pass

    index.insert(0, {
        "bug_id": bug_id,
        "agent": meta["agent"],
        "method": meta["method"],
        "success": meta["success"],
        "elapsed_s": meta["elapsed_s"],
        "timestamp": meta["timestamp"],
    })

    # Keep last 1000 entries
    index = index[:1000]
    with open(index_file, "w") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
