#!/usr/bin/env python3
"""
AgentForge Skill Registry - Skill Discovery and Management

Compatible with:
- Claude MCP (Model Context Protocol) skills
- OpenClaw plugin/skill system
- AgentForge native skills

Functions:
1. Skill discovery: scan all available skills
2. Skill installation: install new skills from local/remote
3. Skill recommendation: recommend skills based on task type
4. Self-update: auto-check and update skills

Start: python3 -m agentforge.skill_registry
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


class SkillRegistry:
    def __init__(self, config=None):
        if config:
            self.skills_dir = config.skills_dir
        else:
            self.skills_dir = Path(
                os.environ.get("SKILLS_DIR", "./skills")
            )
        self.registry_file = self.skills_dir.parent / "skill-registry" / "registry.json"
        self.skills = self._load_registry()

    def _load_registry(self):
        """Load skill registry"""
        if self.registry_file.exists():
            try:
                with open(self.registry_file) as f:
                    return json.load(f)
            except Exception:
                pass

        registry = {
            "version": "1.0.0",
            "last_updated": datetime.now().isoformat(),
            "skills": {},
            "sources": {
                "builtin": str(self.skills_dir / "builtin"),
                "community": str(self.skills_dir / "community"),
                "custom": str(self.skills_dir / "custom"),
            },
        }

        self._scan_and_register(registry)
        self._save_registry(registry)
        return registry

    def _scan_and_register(self, registry):
        """Scan directories and register skills"""
        for category in ["builtin", "community", "custom"]:
            dir_path = self.skills_dir / category
            if dir_path.exists():
                for item in os.listdir(dir_path):
                    skill_path = dir_path / item
                    if (
                        skill_path.is_dir()
                        and (skill_path / "skill.json").exists()
                    ):
                        self._register_skill(registry, str(skill_path), category)

    def _register_skill(self, registry, skill_path, category):
        """Register an AgentForge skill"""
        try:
            with open(os.path.join(skill_path, "skill.json")) as f:
                skill_data = json.load(f)

            skill_id = skill_data.get("id", os.path.basename(skill_path))
            registry["skills"][skill_id] = {
                "id": skill_id,
                "name": skill_data.get("name", skill_id),
                "description": skill_data.get("description", ""),
                "category": category,
                "type": skill_data.get("type", "agentforge"),
                "version": skill_data.get("version", "1.0.0"),
                "path": skill_path,
                "entry_point": skill_data.get("entry_point", ""),
                "triggers": skill_data.get("triggers", []),
                "keywords": skill_data.get("keywords", []),
                "enabled": True,
                "last_updated": datetime.now().isoformat(),
            }
        except Exception:
            pass

    def _save_registry(self, registry):
        """Save registry"""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_file, "w") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)

    def find_skills_for_task(self, task_description):
        """Recommend skills for a task"""
        task_lower = task_description.lower()
        matches = []

        for skill_id, skill in self.skills.get("skills", {}).items():
            if not skill.get("enabled", True):
                continue

            score = 0
            for kw in skill.get("keywords", []):
                if kw.lower() in task_lower:
                    score += 2
            for trigger in skill.get("triggers", []):
                if trigger.lower() in task_lower:
                    score += 3

            if score > 0:
                matches.append((skill_id, score, skill))

        matches.sort(key=lambda x: -x[1])
        return [(sid, skill) for sid, score, skill in matches[:5]]

    def list_all_skills(self, category=None):
        """List all skills"""
        skills = self.skills.get("skills", {})
        if category:
            return {
                k: v
                for k, v in skills.items()
                if v.get("category") == category
            }
        return skills

    def install_skill(self, skill_path, skill_id=None):
        """Install a skill from local path"""
        if os.path.exists(skill_path):
            src = skill_path
            if not skill_id:
                skill_id = os.path.basename(src)

            dest = str(self.skills_dir / "custom" / skill_id)
            if os.path.exists(dest):
                return {
                    "status": "exists",
                    "message": f"Skill {skill_id} already installed",
                }

            subprocess.run(["cp", "-r", src, dest], capture_output=True)
            self._refresh()
            return {"status": "installed", "skill_id": skill_id}

        return {
            "status": "error",
            "message": "Remote installation not yet supported",
        }

    def uninstall_skill(self, skill_id):
        """Uninstall a skill"""
        skill = self.skills.get("skills", {}).get(skill_id)
        if not skill:
            return {
                "status": "error",
                "message": f"Skill {skill_id} not found",
            }

        skill_path = skill.get("path", "")
        if os.path.exists(skill_path):
            subprocess.run(["rm", "-rf", skill_path], capture_output=True)
            del self.skills["skills"][skill_id]
            self._save_registry(self.skills)
            return {"status": "uninstalled", "skill_id": skill_id}

        return {"status": "error", "message": "Skill path not found"}

    def _refresh(self):
        """Refresh skill registry"""
        self.skills["skills"] = {}
        self._scan_and_register(self.skills)
        self._save_registry(self.skills)

    def get_skill_info(self, skill_id):
        """Get skill details"""
        return self.skills.get("skills", {}).get(skill_id)

    def get_statistics(self):
        """Get skill statistics"""
        skills = self.skills.get("skills", {})
        stats = {
            "total": len(skills),
            "by_category": {},
            "by_type": {},
        }
        for skill in skills.values():
            cat = skill.get("category", "unknown")
            type_ = skill.get("type", "unknown")
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            stats["by_type"][type_] = stats["by_type"].get(type_, 0) + 1
        return stats


if __name__ == "__main__":
    import sys

    registry = SkillRegistry()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "list":
            skills = registry.list_all_skills()
            print(f"=== AgentForge 技能列表（共 {len(skills)} 个）===\n")
            for skill_id, skill in sorted(skills.items()):
                enabled = "OK" if skill.get("enabled", True) else "XX"
                print(
                    f"  [{enabled}] [{skill.get('category')}] "
                    f"{skill.get('name')} ({skill_id})"
                )
                print(f"     {skill.get('description', '')[:80]}")
                if skill.get("keywords"):
                    print(f"     关键词: {', '.join(skill.get('keywords', [])[:5])}")
                print()

        elif cmd == "stats":
            stats = registry.get_statistics()
            print(f"=== AgentForge 技能统计 ===")
            print(f"  总技能数: {stats['total']}")
            print(f"  按分类: {stats['by_category']}")
            print(f"  按类型: {stats['by_type']}")

        elif cmd == "find" and len(sys.argv) > 2:
            task = " ".join(sys.argv[2:])
            matches = registry.find_skills_for_task(task)
            print(f"=== 为任务推荐技能: {task[:50]} ===\n")
            for skill_id, skill in matches:
                print(f"  {skill.get('name')} ({skill_id})")
                print(f"     {skill.get('description', '')[:100]}")
                print()

        elif cmd == "install" and len(sys.argv) > 2:
            result = registry.install_skill(sys.argv[2])
            print(f"安装结果: {result}")

        elif cmd == "refresh":
            registry._refresh()
            print("技能注册表已刷新")

        else:
            print(
                "用法: python3 -m agentforge.skill_registry "
                "[list|stats|find <task>|install <path>|info <id>|refresh]"
            )
    else:
        stats = registry.get_statistics()
        print(f"=== AgentForge 技能系统 ===")
        print(f"  总技能数: {stats['total']}")
        print(f"  按分类: {stats['by_category']}")
        print(f"  按类型: {stats['by_type']}")
