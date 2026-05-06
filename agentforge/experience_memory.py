#!/usr/bin/env python3
"""
Experience Memory System - AgentForge Self-Optimization Core

Functions:
1. Records success/failure of each task execution
2. Stores effective tool invocation patterns
3. Remembers user preferences and common issues
4. Optimizes future behavior based on historical performance

Data Structure:
{
  "task_patterns": {
    "Bug查询": {"tool": "zentao-bug-query.sh", "success_rate": 0.95, "avg_time": 2.1}
  },
  "self_reflections": [...],
  "successful_responses": [...],
  "failed_attempts": [...],
  "model_performance": {
    "qwen-plus": {"tasks": 50, "avg_time": 8.2, "quality": 4.2}
  }
}
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path


class ExperienceMemory:
    def __init__(self, agent_id, config=None):
        """
        Args:
            agent_id: Agent identifier
            config: Config instance for path resolution
        """
        self.agent_id = agent_id
        if config:
            self.exp_file = config.get_agent_experience_path(agent_id)
        else:
            self.exp_file = f"./config/agents/{agent_id}/.experience.json"
        self.data = self._load()

    def _load(self):
        """Load experience data"""
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
        """Save experience data"""
        os.makedirs(os.path.dirname(self.exp_file) or ".", exist_ok=True)
        with open(self.exp_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def record_task(
        self,
        task_type,
        success,
        tool_used,
        time_taken,
        quality_score=None,
        error=None,
        improvement=None,
    ):
        """Record task execution result"""
        self.data["total_tasks"] += 1

        if success:
            self.data["successful_tasks"] += 1
        else:
            self.data["failed_tasks"] += 1

        # Update task pattern
        if task_type not in self.data["task_patterns"]:
            self.data["task_patterns"][task_type] = {
                "tool": tool_used,
                "success_count": 0,
                "fail_count": 0,
                "total_time": 0,
                "count": 0,
            }

        pattern = self.data["task_patterns"][task_type]
        if success:
            pattern["success_count"] += 1
        else:
            pattern["fail_count"] += 1
        pattern["total_time"] += time_taken
        pattern["count"] += 1
        pattern["success_rate"] = pattern["success_count"] / pattern["count"]
        pattern["avg_time"] = pattern["total_time"] / pattern["count"]

        # Record success/failure
        if success:
            self.data["successful_responses"].append(
                {
                    "task_type": task_type,
                    "tool": tool_used,
                    "time": time_taken,
                    "quality": quality_score,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self.data["successful_responses"] = self.data["successful_responses"][-50:]
        else:
            self.data["failed_attempts"].append(
                {
                    "task_type": task_type,
                    "tool": tool_used,
                    "error": str(error)[:200],
                    "lesson": improvement,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self.data["failed_attempts"] = self.data["failed_attempts"][-30:]

        self.save()

    def record_reflection(self, task, quality, improvement, time_taken):
        """Record self-reflection"""
        self.data["self_reflections"].append(
            {
                "task": task[:200],
                "quality": quality,
                "improvement": improvement[:300] if improvement else None,
                "time": time_taken,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.data["self_reflections"] = self.data["self_reflections"][-50:]
        self.save()

    def get_best_tool(self, task_type):
        """Recommend best tool based on historical performance"""
        if task_type in self.data["task_patterns"]:
            pattern = self.data["task_patterns"][task_type]
            if pattern["count"] > 2:
                return {
                    "tool": pattern["tool"],
                    "success_rate": pattern.get("success_rate", 0),
                    "avg_time": pattern.get("avg_time", 0),
                    "confidence": "high" if pattern["count"] > 10 else "medium",
                }
        return None

    def get_recent_lessons(self, limit=3):
        """Get recent lessons learned"""
        lessons = []

        for r in self.data["self_reflections"][-limit:]:
            if r.get("improvement"):
                lessons.append(f"改进: {r['improvement']}")

        for f in self.data["failed_attempts"][-limit:]:
            if f.get("lesson"):
                lessons.append(f"教训: {f['lesson']}")

        return lessons

    def get_performance_summary(self):
        """Get performance summary"""
        total = self.data["total_tasks"]
        if total == 0:
            return "暂无执行记录"

        success_rate = self.data["successful_tasks"] / total
        return (
            f"性能报告\n"
            f"  总任务: {total}\n"
            f"  成功率: {success_rate:.1%}\n"
            f"  成功: {self.data['successful_tasks']}\n"
            f"  失败: {self.data['failed_tasks']}\n"
            f"  反思次数: {len(self.data['self_reflections'])}"
        )
