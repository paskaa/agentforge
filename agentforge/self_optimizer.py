#!/usr/bin/env python3
"""
Self-Optimizer - AgentForge Autonomous Improvement System

Functions:
1. Auto-reflection after task completion (evaluate work quality)
2. Experience accumulation and reuse
3. Dynamic strategy adjustment
4. Multi-model intelligent switching
5. Continuous self-improvement loop
"""

import json
import os
import re
import time
import requests
from datetime import datetime
from pathlib import Path


class SelfOptimizer:
    def __init__(self, agent_id, api_key, api_base, model, config=None):
        self.agent_id = agent_id
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.config = config

        # Import experience memory
        from .experience_memory import ExperienceMemory

        if config:
            self.memory = ExperienceMemory(agent_id, config)
            self.soul_path = config.agents_config_dir / agent_id / "SOUL.md"
            self.dynamic_rules_file = config.get_agent_dynamic_rules_path(agent_id)
        else:
            self.memory = ExperienceMemory(agent_id)
            self.soul_path = Path(f"./config/agents/{agent_id}/SOUL.md")
            self.dynamic_rules_file = (
                f"./config/agents/{agent_id}/.dynamic_rules.md"
            )

    def reflect_on_task(
        self, task_description, tool_output, llm_response, time_taken
    ):
        """
        Auto-reflect after task completion

        Flow:
        1. LLM evaluates work quality
        2. Records reflection to experience memory
        3. Adjusts strategy based on quality score
        """
        task_type = self._classify_task(task_description)
        tool_success = bool(
            tool_output
            and "不存在" not in tool_output
            and "失败" not in tool_output
        )
        quality_score = self._self_evaluate(
            task_description, tool_output, llm_response
        )
        improvement = self._generate_improvement(
            task_description, tool_output, llm_response, quality_score
        )

        self.memory.record_task(
            task_type=task_type,
            success=tool_success,
            tool_used=self._detect_tool_used(task_description),
            time_taken=time_taken,
            quality_score=quality_score,
            error=None if tool_success else "工具执行失败",
            improvement=improvement,
        )

        if improvement or quality_score < 4:
            self.memory.record_reflection(
                task=task_description,
                quality=quality_score,
                improvement=improvement,
                time_taken=time_taken,
            )
            if improvement:
                self._update_dynamic_rules(improvement)

        return {
            "quality": quality_score,
            "improvement": improvement,
            "success": tool_success,
        }

    def _classify_task(self, task_description):
        """Auto-classify task type"""
        if re.search(r"Bug|缺陷|问题", task_description, re.IGNORECASE):
            return "Bug查询"
        elif re.search(r"任务|task", task_description, re.IGNORECASE):
            return "任务查询"
        elif re.search(r"禅道|zentao", task_description, re.IGNORECASE):
            return "禅道操作"
        elif re.search(r"飞书|feishu", task_description, re.IGNORECASE):
            return "飞书通知"
        else:
            return "通用任务"

    def _detect_tool_used(self, task_description):
        """Detect which tool was used"""
        if re.search(r"Bug", task_description):
            return "zentao-bug-query.sh"
        elif re.search(r"任务", task_description):
            return "zentao-cli"
        else:
            return "LLM only"

    def _self_evaluate(self, task, tool_output, llm_response):
        """
        LLM self-evaluates work quality (1-5 scale)

        5 = Perfect
        4 = Complete with minor flaws
        3 = Basically complete
        2 = Significant issues
        1 = Incomplete
        """
        prompt = f"""请评估以下任务的完成质量（1-5分）：

任务：{task}
工具输出：{tool_output[:300]}
我的回复：{llm_response[:300]}

评分标准：
5 = 完美完成，完全符合要求
4 = 完成，有小瑕疵
3 = 基本完成，但可以更好
2 = 有较大问题
1 = 未完成

请直接回复数字（1-5），不要解释。"""

        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 5,
                    "temperature": 0.1,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("choices"):
                score = data["choices"][0]["message"]["content"].strip()
                match = re.search(r"([1-5])", score)
                if match:
                    return int(match.group(1))
        except Exception:
            pass

        return 3 if tool_output else 1

    def _generate_improvement(
        self, task, tool_output, llm_response, quality_score
    ):
        """Generate improvement suggestions"""
        if quality_score >= 4:
            return None

        prompt = f"""任务完成质量为 {quality_score}/5 分。

任务：{task}
工具输出：{tool_output[:300]}
我的回复：{llm_response[:300]}

请给出一条具体的改进建议（不超过 50 字）。
格式："下次应该..." 或 "建议先..." 或 "注意..."。
只回复建议内容，不要其他内容。"""

        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.3,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("choices"):
                return data["choices"][0]["message"]["content"].strip()[:200]
        except Exception:
            pass

        return None

    def _update_dynamic_rules(self, improvement):
        """Update dynamic rules file"""
        rules = []
        if os.path.exists(self.dynamic_rules_file):
            with open(self.dynamic_rules_file, encoding="utf-8") as f:
                rules = f.readlines()

        timestamp = datetime.now().strftime("%Y-%m-%d")
        rules.append(f"- {improvement} 【{timestamp} 反思】\n")
        rules = rules[-20:]

        os.makedirs(
            os.path.dirname(self.dynamic_rules_file) or ".", exist_ok=True
        )
        with open(self.dynamic_rules_file, "w", encoding="utf-8") as f:
            f.writelines(rules)

    def get_enhanced_system_prompt(self):
        """
        Generate enhanced system prompt
        Includes: Original SOUL.md + Dynamic rules + Lessons learned + Anti-hallucination
        """
        with open(self.soul_path) as f:
            soul = f.read()

        # Add strict anti-hallucination for project-manager agent
        if self.agent_id == "liubei":
            soul += """

**严格反幻觉指令（最高优先级，违反将被停用）**：
1. 在回复中绝对禁止提及任何用户未明确询问的具体 Bug 编号
2. 绝对禁止编造"该 Bug 不存在"这样的回复
3. Bug 汇总报告必须完全复制查询脚本的输出，不得自行总结
4. 不要主动去查 Bug 并报告不存在——只响应用户的具体请求
"""

        # Add dynamic rules
        if os.path.exists(self.dynamic_rules_file):
            with open(
                self.dynamic_rules_file, encoding="utf-8"
            ) as f:
                rules = f.read()
            if rules.strip():
                soul += "\n\n**经验积累的改进规则**（根据历史任务自动更新）：\n"
                soul += rules

        # Add recent lessons
        lessons = self.memory.get_recent_lessons(3)
        if lessons:
            soul += "\n\n**最近的经验教训**：\n"
            soul += "\n".join(lessons)

        # Add performance stats
        summary = self.memory.get_performance_summary()
        soul += f"\n\n**我的性能统计**：\n{summary}"

        return soul

    def recommend_model(self, task_type):
        """Recommend best model based on historical performance"""
        perf = self.memory.data.get("model_performance", {})
        if not perf:
            return self.model

        best_model = self.model
        best_quality = 0

        for model_name, stats in perf.items():
            quality = stats.get("quality", 0)
            if quality > best_quality:
                best_quality = quality
                best_model = model_name

        return best_model

    def record_model_performance(self, model_name, time_taken, quality_score):
        """Record model performance"""
        if model_name not in self.memory.data["model_performance"]:
            self.memory.data["model_performance"][model_name] = {
                "tasks": 0,
                "total_time": 0,
                "total_quality": 0,
            }

        stats = self.memory.data["model_performance"][model_name]
        stats["tasks"] += 1
        stats["total_time"] += time_taken
        stats["total_quality"] += quality_score
        stats["avg_time"] = stats["total_time"] / stats["tasks"]
        stats["quality"] = stats["total_quality"] / stats["tasks"]

        self.memory.save()

    def print_status(self):
        """Print self-optimization status"""
        summary = self.memory.get_performance_summary()
        print(f"\n{'='*50}")
        print(f"自主优化状态 - {self.agent_id}")
        print(f"{'='*50}")
        print(summary)

        lessons = self.memory.get_recent_lessons(3)
        if lessons:
            print(f"\n最近改进:")
            for l in lessons:
                print(f"  {l}")

        for task_type, pattern in self.memory.data["task_patterns"].items():
            if pattern["count"] > 2:
                print(
                    f"\n{task_type}: 成功率 {pattern.get('success_rate', 0):.1%}, "
                    f"平均时间 {pattern.get('avg_time', 0):.1f}s"
                )
        print()
