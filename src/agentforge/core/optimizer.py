"""
Self-Optimizer - Post-task reflection, dynamic rules, model performance tracking.
"""

import json
import os
import re
import requests
from pathlib import Path
from datetime import datetime


class SelfOptimizer:
    def __init__(self, agent_id, api_key, api_base, model, config=None):
        self.agent_id = agent_id
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.config = config

        from agentforge.core.memory import ExperienceMemory
        if config:
            self.memory = ExperienceMemory(agent_id, config)
            self.soul_path = config.agents_config_dir / agent_id / "agent" / "SOUL.md"
            self.dynamic_rules_file = str(config.agents_config_dir / agent_id / "agent" / ".dynamic_rules.md")
        else:
            self.memory = ExperienceMemory(agent_id)
            base = os.environ.get("AGENTS_CONFIG_DIR", "./config/agents")
            self.soul_path = Path(base) / agent_id / "agent" / "SOUL.md"
            self.dynamic_rules_file = str(Path(base) / agent_id / "agent" / ".dynamic_rules.md")

    def reflect_on_task(self, task_desc, tool_output, llm_response, time_taken):
        task_type = self._classify(task_desc)
        tool_success = bool(tool_output and "不存在" not in tool_output and "失败" not in tool_output)
        quality = self._self_evaluate(task_desc, tool_output, llm_response)
        improvement = self._generate_improvement(task_desc, tool_output, llm_response, quality)

        self.memory.record_task(
            task_type=task_type, success=tool_success,
            tool_used=self._detect_tool(task_desc), time_taken=time_taken,
            quality_score=quality, error=None if tool_success else "工具执行失败",
            improvement=improvement,
        )
        if improvement or quality < 4:
            self.memory.record_reflection(
                task=task_desc, quality=quality,
                improvement=improvement, time_taken=time_taken,
            )
            if improvement:
                self._update_rules(improvement)
        return {"quality": quality, "improvement": improvement, "success": tool_success}

    def _classify(self, desc):
        if re.search(r"Bug|缺陷|问题", desc, re.I): return "Bug查询"
        if re.search(r"任务|task", desc, re.I): return "任务查询"
        if re.search(r"禅道|zentao", desc, re.I): return "禅道操作"
        if re.search(r"飞书|feishu", desc, re.I): return "飞书通知"
        return "通用任务"

    def _detect_tool(self, desc):
        if re.search(r"Bug", desc): return "zentao-bug-query.sh"
        if re.search(r"任务", desc): return "zentao-cli"
        return "LLM only"

    def _self_evaluate(self, task, tool_out, llm_resp):
        prompt = f"""请评估以下任务的完成质量（1-5分）：
任务：{task}\n工具输出：{tool_out[:300]}\n我的回复：{llm_resp[:300]}
评分标准：5=完美 4=小瑕疵 3=基本完成 2=较大问题 1=未完成
请直接回复数字（1-5），不要解释。"""
        try:
            resp = requests.post(f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 5, "temperature": 0.1}, timeout=15)
            data = resp.json()
            if data.get("choices"):
                m = re.search(r"([1-5])", data["choices"][0]["message"]["content"].strip())
                if m: return int(m.group(1))
        except Exception:
            pass
        return 3 if tool_out else 1

    def _generate_improvement(self, task, tool_out, llm_resp, quality):
        if quality >= 4: return None
        prompt = f"""任务完成质量为 {quality}/5 分。\n任务：{task}\n工具输出：{tool_out[:300]}\n我的回复：{llm_resp[:300]}
请给出一条具体的改进建议（不超过 50 字）。格式："下次应该..." 或 "建议先..." 或 "注意..."。
只回复建议内容，不要其他内容。"""
        try:
            resp = requests.post(f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 100, "temperature": 0.3}, timeout=15)
            data = resp.json()
            if data.get("choices"):
                return data["choices"][0]["message"]["content"].strip()[:200]
        except Exception:
            pass
        return None

    def _update_rules(self, improvement):
        rules = []
        if os.path.exists(self.dynamic_rules_file):
            with open(self.dynamic_rules_file, encoding="utf-8") as f:
                rules = f.readlines()
        rules.append(f"- {improvement} 【{datetime.now().strftime('%Y-%m-%d')} 反思】\n")
        rules = rules[-20:]
        os.makedirs(os.path.dirname(self.dynamic_rules_file) or ".", exist_ok=True)
        with open(self.dynamic_rules_file, "w", encoding="utf-8") as f:
            f.writelines(rules)

    def get_enhanced_system_prompt(self):
        with open(self.soul_path) as f:
            soul = f.read()
        if self.agent_id == "liubei":
            soul += "\n\n**严格反幻觉指令（最高优先级）**：\n1. 禁止提及用户未询问的 Bug 编号\n2. 禁止编造'该 Bug 不存在'的回复\n3. Bug 汇总必须完全复制脚本输出\n4. 只响应用户的具体请求\n"
        if os.path.exists(self.dynamic_rules_file):
            with open(self.dynamic_rules_file, encoding="utf-8") as f:
                rules = f.read()
            if rules.strip():
                soul += f"\n\n**经验积累的改进规则**：\n{rules}"
        lessons = self.memory.get_recent_lessons(3)
        if lessons:
            soul += "\n\n**最近的经验教训**：\n" + "\n".join(lessons)
        soul += f"\n\n**我的性能统计**：\n{self.memory.get_performance_summary()}"
        return soul

    def record_model_performance(self, model_name, time_taken, quality_score):
        if model_name not in self.memory.data["model_performance"]:
            self.memory.data["model_performance"][model_name] = {"tasks": 0, "total_time": 0, "total_quality": 0}
        s = self.memory.data["model_performance"][model_name]
        s["tasks"] += 1; s["total_time"] += time_taken; s["total_quality"] += quality_score
        s["avg_time"] = s["total_time"] / s["tasks"]; s["quality"] = s["total_quality"] / s["tasks"]
        self.memory.save()

    def print_status(self):
        print(f"\n{'='*50}\n自主优化状态 - {self.agent_id}\n{'='*50}")
        print(self.memory.get_performance_summary())
        for t, p in self.memory.data["task_patterns"].items():
            if p["count"] > 2:
                print(f"\n{t}: 成功率 {p.get('success_rate', 0):.1%}, 平均时间 {p.get('avg_time', 0):.1f}s")
