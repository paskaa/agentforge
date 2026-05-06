"""
Skill Registry - Skill discovery, installation, and recommendation.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


class SkillRegistry:
    def __init__(self, skills_dir=None):
        self.skills_dir = Path(skills_dir or os.environ.get("SKILLS_DIR", "./skills"))
        self.registry_file = self.skills_dir / "registry.json"
        self.skills = self._load()

    def _load(self):
        if self.registry_file.exists():
            try:
                with open(self.registry_file) as f:
                    return json.load(f)
            except Exception:
                pass
        registry = {"version": "1.0.0", "last_updated": datetime.now().isoformat(),
                     "skills": {}, "sources": {
            "builtin": str(self.skills_dir / "builtin"),
            "community": str(self.skills_dir / "community"),
            "custom": str(self.skills_dir / "custom"),
        }}
        self._scan(registry)
        self._save(registry)
        return registry

    def _scan(self, registry):
        for cat in ["builtin", "community", "custom"]:
            d = self.skills_dir / cat
            if d.exists():
                for item in os.listdir(d):
                    sp = d / item
                    if sp.is_dir() and (sp / "skill.json").exists():
                        try:
                            with open(sp / "skill.json") as f:
                                sd = json.load(f)
                            sid = sd.get("id", item)
                            registry["skills"][sid] = {
                                "id": sid, "name": sd.get("name", sid),
                                "description": sd.get("description", ""),
                                "category": cat, "type": sd.get("type", "agentforge"),
                                "version": sd.get("version", "1.0.0"),
                                "path": str(sp), "entry_point": sd.get("entry_point", ""),
                                "triggers": sd.get("triggers", []),
                                "keywords": sd.get("keywords", []),
                                "enabled": True, "last_updated": datetime.now().isoformat(),
                            }
                        except Exception:
                            pass

    def _save(self, registry):
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_file, "w") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)

    def find_for_task(self, task_desc):
        t = task_desc.lower()
        matches = []
        for sid, skill in self.skills.get("skills", {}).items():
            if not skill.get("enabled", True):
                continue
            score = sum(2 for kw in skill.get("keywords", []) if kw.lower() in t)
            score += sum(3 for tr in skill.get("triggers", []) if tr.lower() in t)
            if score > 0:
                matches.append((sid, score, skill))
        matches.sort(key=lambda x: -x[1])
        return [(s, sk) for s, _, sk in matches[:5]]

    def list_all(self, category=None):
        skills = self.skills.get("skills", {})
        if category:
            return {k: v for k, v in skills.items() if v.get("category") == category}
        return skills

    def get_statistics(self):
        skills = self.skills.get("skills", {})
        stats = {"total": len(skills), "by_category": {}, "by_type": {}}
        for s in skills.values():
            c = s.get("category", "unknown"); t = s.get("type", "unknown")
            stats["by_category"][c] = stats["by_category"].get(c, 0) + 1
            stats["by_type"][t] = stats["by_type"].get(t, 0) + 1
        return stats
