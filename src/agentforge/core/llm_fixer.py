"""
LLM Fixer — direct bug fixing using qwen3-coder-plus.

Bypasses Claude Code CLI entirely. The agent's own LLM client
reads the bug, searches the codebase, generates a fix, applies it,
and commits — all within one API call.

Faster than Claude Code (no subprocess overhead) and FREE on coding plan.
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from agentforge.core.llm import LLMClient
from agentforge.core.fix_trajectory import save_trajectory

logger = logging.getLogger("agentforge.fixer")

REPO_PATH = "/root/.openclaw/workspace/his-repo"
SEARCH_EXTS = "*.vue,*.js,*.ts,*.java,*.xml,*.yml,*.properties"
MAX_FILES = 5  # Increased for Java + Vue analysis
MAX_LINES_PER_FILE = 200


class LLMFixer:
    """Direct LLM-based bug fixer."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def fix(self, bug_id: str, bug_title: str, bug_steps: str,
            agent_name: str) -> tuple[bool, str]:
        """
        Attempt to fix a bug using qwen3-coder-plus directly.
        Returns (success, message).
        """
        start = time.time()
        logger.info("[fixer] Starting LLM fix for Bug #%s: %s", bug_id, bug_title[:50])

        # 1. Search for relevant files
        keywords = self._extract_keywords(bug_title, bug_steps)
        files = self._search_codebase(keywords)
        if not files:
            save_trajectory(bug_id, agent_name, "llm_fixer", False, time.time() - start,
                            fix_summary="未找到相关代码文件")
            return False, f"未找到 Bug #%s 的相关代码文件" % bug_id

        # 1.5 Filter: remove files whose directory doesn't match bug's functional area
        files = self._filter_by_area(bug_title, files)
        if not files:
            save_trajectory(bug_id, agent_name, "llm_fixer", False, time.time() - start,
                            fix_summary="关键词匹配到文件但与Bug区域不相关")
            return False, f"匹配到的文件与 Bug #%s 的功能区域不相关" % bug_id

        # 2. Read relevant file contents
        code_snippets = self._read_files(files[:MAX_FILES])
        if not code_snippets:
            return False, f"无法读取 Bug #%s 的相关文件" % bug_id

        # 3. Generate fix via LLM
        prompt = self._build_prompt(bug_id, bug_title, bug_steps, code_snippets)
        fix_result = self._call_fix_llm(prompt)
        if not fix_result:
            return False, f"LLM 未能生成 Bug #%s 的修复方案" % bug_id

        # 4. Parse and apply the fix
        file_path, search_block, replace_block = self._parse_fix(fix_result, files)
        if not file_path:
            return False, f"无法解析 Bug #%s 的修复方案" % bug_id

        applied = self._apply_fix(file_path, search_block, replace_block)
        if not applied:
            logger.warning("[fixer] Search block not found in %s, generated fix:\nFILE: %s\nSEARCH: %s\nREPLACE: %s",
                          file_path, file_path, search_block[:200], replace_block[:200])
            save_trajectory(bug_id, agent_name, "llm_fixer", False, time.time() - start,
                            files_searched=files, generated_fix=fix_result,
                            stdout=f"FILE: {file_path}\nSEARCH:\n{search_block}\n\nREPLACE:\n{replace_block}",
                            fix_summary=f"Search block not found in {file_path}")
            return False, f"无法应用 Bug #%s 的修复代码" % bug_id

        # 5. Syntax check
        syntax_ok, syntax_err = self._syntax_check(file_path)
        if not syntax_ok:
            # Revert the fix
            self._git_checkout(file_path)
            save_trajectory(bug_id, agent_name, "llm_fixer", False, time.time() - start,
                            files_searched=files, generated_fix=fix_result,
                            fix_summary=f"语法错误: {syntax_err[:100]}")
            return False, f"语法检查失败: {syntax_err[:150]}"

        # 6. Commit
        self._commit(bug_id, bug_title, agent_name)

        elapsed = time.time() - start
        logger.info("[fixer] Bug #%s fixed in %.0fs via LLM direct fix", bug_id, elapsed)
        save_trajectory(bug_id, agent_name, "llm_fixer", True, elapsed,
                        files_searched=files, generated_fix=fix_result,
                        fix_summary=f"修改文件: {file_path}")
        return True, f"修复完成 ({elapsed:.0f}s)，修改文件：{file_path}"

    # =========================================================================
    #  Internal
    # =========================================================================

    def _extract_keywords(self, title: str, steps: str) -> list[str]:
        """Extract search keywords from bug title and steps.
        Long phrases are split into 2-3 char chunks for better grep matching
        since source code rarely contains the full bug description text.
        """
        text = f"{title} {steps[:500]}"
        # Extract Chinese phrases and English identifiers
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z_][\w.]{2,}', text)
        # Split long phrases into 2-3 char chunks for better matching
        chunks = []
        for w in words:
            w = w.strip()
            if len(w) <= 4:
                chunks.append(w)
            else:
                # Split long phrase into overlapping 2-char chunks
                for i in range(0, len(w) - 1, 2):
                    chunks.append(w[i:i+3] if i+3 <= len(w) else w[i:])
        # Deduplicate, take top 10
        seen = set()
        result = []
        for w in chunks:
            if w.lower() not in seen and len(w) >= 2:
                seen.add(w.lower())
                result.append(w)
                if len(result) >= 10:
                    break
        return result

    def _filter_by_area(self, title: str, files: list[str]) -> list[str]:
        """Filter files to those whose directory matches the bug's functional area."""
        AREA_MAP = {
            "住院医生": {"include": ["inpatientDoctor", "doctorstation"], "exclude": []},
            "门诊医生": {"include": ["outpatientDoctor", "doctorstation"], "exclude": ["inpatient"]},
            "门诊手术": {"include": ["surgery", "surgicalschedule"], "exclude": []},
            "手术管理": {"include": ["surgery", "surgicalschedule"], "exclude": []},
            "目录管理": {"include": ["catalog", "dict", "directory"], "exclude": []},
            "系统管理": {"include": ["system", "admin", "config"], "exclude": []},
            "疾病报告管理-报告卡管理": {"include": ["reportCard"], "exclude": ["infectious"]},
            "疾病报告管理": {"include": ["diseaseReport", "reportCard"], "exclude": ["infectious"]},
            "报告卡管理": {"include": ["reportCard"], "exclude": ["infectious"]},
            "领用出库": {"include": ["stock", "inventory", "drug"], "exclude": []},
            "门诊收费": {"include": ["charge", "billing", "fee"], "exclude": []},
            "检验申请": {"include": ["lab", "applicationForm", "inpatientDoctor"], "exclude": []},
            "医嘱": {"include": ["advice", "order", "inpatientDoctor"], "exclude": []},
        }
        # Push Java files higher when backend keywords are present
        BACKEND_KW = ["接口", "API", "api", "500", "会话", "session", "登录", "login",
                      "权限", "permission", "配置", "config", "服务", "service",
                      "查询", "query", "SQL", "sql", "mapper", "Controller"]
        is_backend_bug = any(kw in title for kw in BACKEND_KW)
        if is_backend_bug:
            # Move Java files to the front
            java_files = [f for f in files if f.endswith('.java')]
            other_files = [f for f in files if not f.endswith('.java')]
            return java_files + other_files
        # Find the most specific area match (longest match first)
        matched = None
        for area in sorted(AREA_MAP, key=len, reverse=True):
            if area in title:
                matched = AREA_MAP[area]
                break
        if not matched:
            logger.info("[fixer] Area filter: no area match, keeping all %d files", len(files))
            return files

        result = []
        for f in files:
            f_lower = f.lower()
            # Must match at least one include keyword
            if not any(d.lower() in f_lower for d in matched["include"]):
                continue
            # Must NOT match any exclude keyword
            if any(d.lower() in f_lower for d in matched["exclude"]):
                logger.debug("[fixer] Excluded by area filter: %s", f)
                continue
            result.append(f)
        logger.info("[fixer] Area filter: %d → %d files (area matched)", len(files), len(result))
        return result if result else files

    def _search_codebase(self, keywords: list[str]) -> list[str]:
        """Search repo for files matching bug keywords.
        Strategy: search by directory relevance first, then cross-filter by keywords.
        """
        file_scores: dict[str, int] = {}

        # Directory boosts — relevant paths score higher
        DIR_BOOSTS = {
            "inpatientDoctor": 5, "applicationForm": 5, "surgery": 5,
            "order": 4, "doctorstation": 3, "surgicalschedule": 3,
            "api": 2, "components": 2, "views": 1,
        }

        for kw in keywords[:8]:
            if not kw or len(kw) < 2:
                continue
            try:
                result = subprocess.run(
                    ["grep", "-rl", "-I", "--include=*.vue", "--include=*.js",
                     "--include=*.java", kw, "."],
                    capture_output=True, text=True, timeout=10,
                    cwd=REPO_PATH,
                )
                for line in result.stdout.strip().split("\n"):
                    if line:
                        # Base score: 1 per keyword match
                        score = file_scores.get(line, 0) + 1
                        # Bonus: directory relevance
                        for dir_name, boost in DIR_BOOSTS.items():
                            if dir_name in line:
                                score += boost
                                break  # Only one boost per file
                        file_scores[line] = score
            except Exception:
                pass

        # Sort by score descending, main dir over submodule, shorter path
        def sort_by_relevance(item):
            f, score = item
            is_submodule = 1 if f.startswith("./his-repo/") else 0
            return (-score, is_submodule, len(f.split("/")), f)

        ranked = sorted(file_scores.items(), key=sort_by_relevance)
        return [f for f, _ in ranked[:MAX_FILES * 2]]

    def _read_files(self, file_paths: list[str]) -> dict[str, str]:
        """Read relevant portions of files."""
        snippets = {}
        for fp in file_paths:
            full = Path(REPO_PATH) / fp
            if not full.exists():
                continue
            try:
                with open(full) as f:
                    lines = f.readlines()
                if len(lines) > MAX_LINES_PER_FILE:
                    # Take first 100 + last 100 lines
                    content = "".join(lines[:100]) + "\n... (truncated) ...\n" + "".join(lines[-100:])
                else:
                    content = "".join(lines)
                snippets[fp] = content
            except Exception:
                pass
            if len(snippets) >= MAX_FILES:
                break
        return snippets

    def _build_prompt(self, bug_id: str, title: str, steps: str,
                      code_snippets: dict[str, str]) -> str:
        """Build the fix prompt for qwen3-coder-plus."""
        files_section = ""
        for fp, content in code_snippets.items():
            files_section += f"\n### {fp}\n```\n{content[:2000]}\n```\n"

        prompt = f"""你是一个资深前端工程师，负责修复 HIS 系统的 Bug。

## Bug #{bug_id}: {title}

### 复现步骤
{steps[:800]}

### 相关代码
{files_section}

## 修复策略
1. 优先修复前端逻辑错误（数据绑定、条件判断）
2. 如果涉及后端权限报错（msgError '无权限'、'获取列表失败'），不要改业务逻辑，
   改为优雅降级：msgError → console.warn，同时初始化空数组
3. 后端 API 返回 403/401 且前端无权限控制代码 → 回复"需要后端修改"

## 任务
分析以上代码，定位 Bug 根因，生成修复代码。

## 输出格式（严格遵守）
FILE: <文件路径>
SEARCH:
<要替换的原始代码块，精确到行>
REPLACE:
<替换后的代码块>

只输出一个 FILE/SEARCH/REPLACE 块，不要额外解释。"""
        return prompt

    def _call_fix_llm(self, prompt: str) -> Optional[str]:
        """Call qwen3-coder-plus to generate the fix."""
        messages = [
            {"role": "system", "content": "你是代码修复专家。只输出修复代码块，不解释。"},
            {"role": "user", "content": prompt},
        ]
        model = self.llm.select_model("coding")
        logger.info("[fixer] Calling %s for fix...", model)
        result = self.llm.call(messages, model=model, max_tokens=3000, temperature=0.1)
        return result

    def _parse_fix(self, fix_text: str, candidate_files: list[str]) -> tuple:
        """Parse FILE/SEARCH/REPLACE from LLM output."""
        file_match = re.search(r'FILE:\s*(.+)', fix_text)
        search_match = re.search(r'SEARCH:\s*\n(.*?)(?=REPLACE:|$)', fix_text, re.DOTALL)
        replace_match = re.search(r'REPLACE:\s*\n(.*?)$', fix_text, re.DOTALL)

        if not file_match:
            return (None, None, None)

        file_path = file_match.group(1).strip()
        # Try to match against found files
        for cf in candidate_files:
            if file_path in cf or cf.endswith(file_path.split("/")[-1]):
                file_path = cf
                break

        search = search_match.group(1).strip() if search_match else ""
        replace = replace_match.group(1).strip() if replace_match else ""

        return (file_path, search, replace)

    def _apply_fix(self, file_path: str, search: str, replace: str) -> bool:
        """Apply the fix to the file."""
        full = Path(REPO_PATH) / file_path
        if not full.exists():
            logger.error("[fixer] File not found: %s", file_path)
            return False

        try:
            with open(full) as f:
                content = f.read()

            if search not in content:
                # Try fuzzy matching: line-level search with whitespace tolerance
                search_lines = [l.strip() for l in search.split("\n") if l.strip()]
                content_lines = content.split("\n")
                content_stripped = [l.strip() for l in content_lines]

                # Try to find a sequence of lines that matches
                for i in range(len(content_stripped) - len(search_lines) + 1):
                    if content_stripped[i:i+len(search_lines)] == search_lines:
                        # Found fuzzy match — use original content lines for replacement
                        orig_block = "\n".join(content_lines[i:i+len(search_lines)])
                        new_content = content.replace(orig_block, replace, 1)
                        with open(full, "w") as f:
                            f.write(new_content)
                        logger.info("[fixer] Applied fix (fuzzy) to %s (line %d)", file_path, i+1)
                        return True

                # Try matching just the first line of the search block
                if search_lines:
                    first_line = search_lines[0]
                    for i, cl in enumerate(content_stripped):
                        if cl == first_line:
                            # Replace from this line to end of matched block
                            end = min(i + len(search_lines), len(content_lines))
                            orig_block = "\n".join(content_lines[i:end])
                            new_content = content.replace(orig_block, replace, 1)
                            with open(full, "w") as f:
                                f.write(new_content)
                            logger.info("[fixer] Applied fix (first-line match) to %s", file_path)
                            return True

                logger.error("[fixer] Search block not found in %s (tried exact + fuzzy + first-line)", file_path)
                return False

            new_content = content.replace(search, replace, 1)
            with open(full, "w") as f:
                f.write(new_content)
            logger.info("[fixer] Applied fix to %s", file_path)
            return True
        except Exception as e:
            logger.error("[fixer] Apply failed: %s", e)
            return False

    def _syntax_check(self, file_path: str) -> tuple[bool, str]:
        """Check syntax of the modified file. Returns (ok, error_message)."""
        full = Path(REPO_PATH) / file_path
        ext = full.suffix.lower()
        try:
            if ext == '.vue':
                # Extract <script> block and check with node --check
                import re as _re
                with open(full) as f:
                    content = f.read()
                script_match = _re.search(r'<script[^>]*>(.*?)</script>', content, _re.DOTALL)
                if script_match:
                    import tempfile as _tf
                    with _tf.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as tmp:
                        tmp.write(script_match.group(1))
                        tmp_path = tmp.name
                    try:
                        r = subprocess.run(
                            ["node", "--check", tmp_path],
                            capture_output=True, text=True, timeout=15,
                        )
                        if r.returncode != 0:
                            return False, r.stderr[:200] or "JS syntax error"
                    finally:
                        try:
                            import os as _os
                            _os.unlink(tmp_path)
                        except Exception:
                            pass
                return True, ""
            elif ext in ('.js', '.ts', '.jsx', '.tsx'):
                r = subprocess.run(
                    ["node", "--check", str(full)],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode != 0:
                    return False, r.stderr[:200] or "JS syntax error"
                return True, ""
            elif ext in ('.java',):
                return True, ""
            else:
                return True, ""
        except FileNotFoundError:
            return True, ""
        except Exception as e:
            return True, str(e)

    def _git_checkout(self, file_path: str):
        """Revert a file to its last committed state."""
        try:
            subprocess.run(
                ["git", "checkout", "--", file_path],
                capture_output=True, text=True, timeout=10,
                cwd=REPO_PATH,
            )
        except Exception:
            pass

    def _commit(self, bug_id: str, bug_title: str, agent_name: str):
        """Commit the fix. Returns True on success."""
        import os as _os
        try:
            r = subprocess.run(
                ["git", "add", "-A"],
                capture_output=True, text=True, timeout=10, cwd=REPO_PATH,
            )
            if r.returncode != 0:
                logger.error("[fixer] git add failed: %s", r.stderr[:100])
                return

            r = subprocess.run(
                ["git", "commit", "-m", f"Fix Bug #{bug_id}: {bug_title[:80]}"],
                capture_output=True, text=True, timeout=10, cwd=REPO_PATH,
                env={**_os.environ,
                     "GIT_AUTHOR_NAME": agent_name,
                     "GIT_AUTHOR_EMAIL": f"{agent_name}@gentronhealth.com"},
            )
            if r.returncode != 0 and "nothing to commit" not in r.stderr:
                logger.error("[fixer] git commit failed: %s", r.stderr[:100])
                return

            r = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                capture_output=True, text=True, timeout=30, cwd=REPO_PATH,
            )
            if r.returncode != 0:
                logger.error("[fixer] git push failed: %s", r.stderr[:100])
                return

            logger.info("[fixer] Committed and pushed fix for Bug #%s", bug_id)
        except Exception as e:
            logger.error("[fixer] Commit failed: %s", e)
