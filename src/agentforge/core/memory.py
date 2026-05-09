"""
Experience Memory — thread-safe persistent experience storage.

Records task success/failure, stores tool patterns,
remembers lessons, optimizes future behavior.

Uses fcntl file locking to prevent concurrent write corruption.
"""

import fcntl
import json
import os
from datetime import datetime
from typing import Optional


class ExperienceMemory:
    def __init__(self, agent_id: str, config: Optional[object] = None):
        self.agent_id = agent_id
        if config and hasattr(config, "get_agent_experience_path"):
            self.exp_file = config.get_agent_experience_path(agent_id)
        else:
            base = os.environ.get("AGENTS_CONFIG_DIR", "./config/agents")
            self.exp_file = f"{base}/{agent_id}/agent/.experience.json"
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.exp_file):
            try:
                with open(self.exp_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "task_patterns": {},
            "self_reflections": [],
            "successful_responses": [],
            "failed_attempts": [],
            "model_performance": {},
            "created_at": datetime.now().isoformat(),
            "total_tasks": 0,
            "successful_tasks": 0,
            "failed_tasks": 0,
        }

    def save(self):
        """Thread-safe save using fcntl advisory lock."""
        os.makedirs(os.path.dirname(self.exp_file) or ".", exist_ok=True)
        try:
            with open(self.exp_file, "w", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            # Fallback for non-UNIX or unavailable fcntl
            with open(self.exp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def record_task(self, task_type: str, success: bool, tool_used: str,
                    time_taken: float, quality_score: Optional[int] = None,
                    error: Optional[str] = None, improvement: Optional[str] = None):
        self.data["total_tasks"] += 1
        if success:
            self.data["successful_tasks"] += 1
        else:
            self.data["failed_tasks"] += 1

        if task_type not in self.data["task_patterns"]:
            self.data["task_patterns"][task_type] = {
                "tool": tool_used, "success_count": 0, "fail_count": 0,
                "total_time": 0, "count": 0,
            }
        p = self.data["task_patterns"][task_type]
        if success:
            p["success_count"] += 1
        else:
            p["fail_count"] += 1
        p["total_time"] += time_taken
        p["count"] += 1
        p["success_rate"] = p["success_count"] / p["count"]
        p["avg_time"] = p["total_time"] / p["count"]

        if success:
            self.data["successful_responses"].append({
                "task_type": task_type, "tool": tool_used,
                "time": time_taken, "quality": quality_score,
                "timestamp": datetime.now().isoformat(),
            })
            self.data["successful_responses"] = self.data["successful_responses"][-50:]
        else:
            self.data["failed_attempts"].append({
                "task_type": task_type, "tool": tool_used,
                "error": str(error)[:200], "lesson": improvement,
                "timestamp": datetime.now().isoformat(),
            })
            self.data["failed_attempts"] = self.data["failed_attempts"][-30:]
        self.save()

    def record_reflection(self, task: str, quality: int,
                          improvement: Optional[str], time_taken: float):
        self.data["self_reflections"].append({
            "task": task[:200], "quality": quality,
            "improvement": improvement[:300] if improvement else None,
            "time": time_taken, "timestamp": datetime.now().isoformat(),
        })
        self.data["self_reflections"] = self.data["self_reflections"][-50:]
        self.save()

    def get_best_tool(self, task_type: str) -> Optional[dict]:
        if task_type in self.data["task_patterns"]:
            p = self.data["task_patterns"][task_type]
            if p["count"] > 2:
                return {
                    "tool": p["tool"], "success_rate": p.get("success_rate", 0),
                    "avg_time": p.get("avg_time", 0),
                    "confidence": "high" if p["count"] > 10 else "medium",
                }
        return None

    def get_recent_lessons(self, limit: int = 3) -> list[str]:
        lessons = []
        for r in self.data["self_reflections"][-limit:]:
            if r.get("improvement"):
                lessons.append(f"改进: {r['improvement']}")
        for f in self.data["failed_attempts"][-limit:]:
            if f.get("lesson"):
                lessons.append(f"教训: {f['lesson']}")
        return lessons

    def get_performance_summary(self) -> str:
        total = self.data["total_tasks"]
        if total == 0:
            return "暂无执行记录"
        rate = self.data["successful_tasks"] / total
        return (f"性能报告\n  总任务: {total}\n  成功率: {rate:.1%}\n"
                f"  成功: {self.data['successful_tasks']}\n"
                f"  失败: {self.data['failed_tasks']}\n"
                f"  反思次数: {len(self.data['self_reflections'])}")
