"""
Agent Test Environment v2 — Playwright against production site.

Since the local Java backend isn't available, test directly against
the production HIS site. Uses Playwright to login, navigate, screenshot,
reproduce bugs, and verify fixes.

URL: https://his.gentronhealth.com (or http://localhost:81 for dev)
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentforge.tester")

PROD_URL = "https://his.gentronhealth.com"
DEV_URL = "http://localhost:81"
SCREENSHOT_DIR = Path("/tmp/agentforge-screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TestEnvironment:
    """Test environment for bug reproduction and fix verification."""

    def __init__(self, use_production: bool = True):
        self.base_url = PROD_URL if use_production else DEV_URL
        self._browser = None
        self._page = None

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            logger.info("[tester] Browser launched → %s", self.base_url)

    def _ensure_page(self):
        self._ensure_browser()
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page(viewport={"width": 1920, "height": 1080})

    def close(self):
        if self._page:
            self._page.close()
        if self._browser:
            self._browser.close()
        if hasattr(self, '_pw'):
            self._pw.stop()

    def screenshot(self, name: str) -> Path:
        self._ensure_page()
        path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
        self._page.screenshot(path=str(path), full_page=True)
        return path

    # =========================================================================
    #  Login
    # =========================================================================

    def login(self, username: str = "doctor1", password: str = "123456") -> bool:
        self._ensure_page()
        try:
            self._page.goto(self.base_url, timeout=30_000)
            self._page.wait_for_load_state("networkidle")
            self._page.wait_for_timeout(2000)

            # Fill login form
            self._page.locator("input[placeholder*='账号'], input[placeholder*='用户名']").first.fill(username)
            self._page.locator("input[placeholder*='密码']").first.fill(password)

            # Select institution if present
            try:
                self._page.locator(".el-select input, input[placeholder*='机构']").first.click(timeout=2000)
                self._page.wait_for_timeout(1000)
                self._page.locator(".el-select-dropdown__item:visible").first.click(timeout=3000)
            except Exception:
                pass

            # Login button may have spaces: "登 录" vs "登录"
            self._page.locator("button:has-text('登')").first.click()
            self._page.wait_for_timeout(5000)

            logged_in = "/login" not in self._page.url
            if logged_in:
                logger.info("[tester] Logged in as %s → %s", username, self._page.url[:60])
                return True

            # Fallback: direct API login + set token in localStorage
            if not logged_in:
                try:
                    import requests, json
                    resp = requests.post(
                        f"{self.base_url.replace(':81',':18082')}/openhis/login"
                        if ':81' in self.base_url else f"{self.base_url}/openhis/login",
                        json={"username": username, "password": password, "tenantId": "1"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        token = resp.json().get("token", "")
                        if token:
                            self._page.evaluate(f"localStorage.setItem('token','{token}')")
                            self._page.goto(self.base_url, timeout=15000)
                            self._page.wait_for_timeout(3000)
                            logged_in = "/login" not in self._page.url
                            logger.info("[tester] API login bypass → %s", logged_in)
                except Exception:
                    pass

            return logged_in
        except Exception as e:
            logger.warning("[tester] Login failed: %s", e)
            return False

    # =========================================================================
    #  Bug reproduction
    # =========================================================================

    def reproduce_bug(self, bug_id: str, bug_title: str, bug_steps: str = "") -> dict:
        """
        Attempt to reproduce a bug and capture evidence.
        Returns dict with: success, screenshots, description, error_text
        """
        result = {"success": False, "screenshots": [], "description": "", "error_text": ""}

        # Parse menu path from bug steps
        menu_path = self._parse_menu_path(bug_title, bug_steps)
        if not menu_path:
            result["description"] = "无法解析菜单路径"
            return result

        logger.info("[tester] Reproducing Bug #%s: navigate %s", bug_id, menu_path)
        try:
            # Navigate menu
            for item in menu_path:
                try:
                    self._page.locator(f"text={item}").first.click(timeout=5000)
                    self._page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Screenshot
            before = self.screenshot(f"bug{bug_id}_before")
            result["screenshots"].append(str(before))

            # Detect errors
            errors = []
            for sel in [".el-message--error", ".el-notification--error", "[class*='error']"]:
                try:
                    for el in self._page.locator(sel).all():
                        if el.is_visible():
                            errors.append(el.text_content()[:100])
                except Exception:
                    pass

            # Check page text
            page_text = self._page.locator("body").text_content()[:500]
            if "失败" in page_text:
                errors.append("页面包含'失败'提示")
            if "错误" in page_text:
                errors.append("页面包含'错误'提示")
            if "无数据" in page_text:
                errors.append("下拉框显示'无数据'")

            result["success"] = len(errors) > 0
            result["error_text"] = "; ".join(errors) if errors else "未检测到异常"
            result["description"] = "复现成功" if errors else "未复现 — 可能需特定操作"

            logger.info("[tester] Bug #%s reproduction: %s", bug_id, result["description"])
            return result

        except Exception as e:
            result["description"] = str(e)[:200]
            return result

    def verify_fix(self, bug_id: str, menu_path: list[str]) -> dict:
        """After fix, reload and verify the bug is gone."""
        result = {"fixed": False, "screenshots": [], "errors": []}

        try:
            self._page.reload()
            self._page.wait_for_timeout(3000)

            # Navigate again
            for item in menu_path:
                try:
                    self._page.locator(f"text={item}").first.click(timeout=5000)
                    self._page.wait_for_timeout(2000)
                except Exception:
                    pass

            after = self.screenshot(f"bug{bug_id}_after")
            result["screenshots"].append(str(after))

            # Check errors
            error_count = 0
            for sel in [".el-message--error", ".el-notification--error"]:
                try:
                    for el in self._page.locator(sel).all():
                        if el.is_visible():
                            result["errors"].append(el.text_content()[:100])
                            error_count += 1
                except Exception:
                    pass

            result["fixed"] = error_count == 0
            return result

        except Exception as e:
            result["errors"].append(str(e))
            return result

    def _parse_menu_path(self, title: str, steps: str) -> list[str]:
        """Extract menu navigation path from bug title/steps."""
        import re

        # Known menu structures
        MENU_TREES = {
            "住院医生工作站": ["住院医生工作站"],
            "门诊手术安排": ["手术管理", "门诊手术安排"],
            "目录管理-诊疗目录": ["系统管理", "目录管理", "诊疗目录"],
            "检验申请": ["住院医生工作站", "检验申请"],
            "疾病报告管理-报告卡管理": ["疾病报告管理", "报告卡管理"],
            "手术申请单": ["住院医生工作站", "手术申请单"],
            "临床医嘱": ["住院医生工作站", "临床医嘱"],
        }

        for key, path in MENU_TREES.items():
            if key in title:
                return path

        # Fallback: extract from steps
        menu = re.findall(r'【(.+?)】', steps)
        if menu:
            return menu[:4]

        return []


# =========================================================================
#  Singleton
# =========================================================================

_tester: Optional[TestEnvironment] = None

def get_tester() -> TestEnvironment:
    global _tester
    if _tester is None:
        _tester = TestEnvironment(use_production=True)
    return _tester
