"""
Bug Image Extractor — extract screenshots from zentao bug descriptions.

Zentao bugs often have critical error messages embedded in screenshots
(not in text). This module extracts image URLs from bug HTML,
downloads them, and makes them available for diagnosis.

Uses zentao API token + requests.
"""

import hashlib
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("agentforge.images")

ZENTAO_URL = "https://zentao.gentronhealth.com"
IMAGE_CACHE = Path("/tmp/agentforge-bug-images")
IMAGE_CACHE.mkdir(parents=True, exist_ok=True)


def _get_token() -> Optional[str]:
    """Get zentao API token from config."""
    token_file = os.path.expanduser("~/.config/zentao/.env")
    try:
        with open(token_file) as f:
            for line in f:
                if line.startswith("ZENTAO_TOKEN="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def extract_image_urls(bug_html: str) -> list[str]:
    """Extract full image URLs from zentao bug HTML content."""
    if not bug_html:
        return []
    soup = BeautifulSoup(bug_html, "html.parser")
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue
        # Make absolute URL
        if src.startswith("http"):
            full_url = src
        elif src.startswith("/"):
            full_url = urljoin(ZENTAO_URL, src)
        else:
            full_url = urljoin(ZENTAO_URL + "/", src)
        urls.append(full_url)
    return urls


def download_image(url: str, bug_id: str, index: int = 0) -> Optional[Path]:
    """Download a bug image with zentao authentication. Returns local path."""
    token = _get_token()
    if not token:
        logger.warning("[images] No zentao token, can't download %s", url[:60])
        return None

    # Generate cache filename
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = ".png"
    if ".jpg" in url.lower():
        ext = ".jpg"
    elif ".jpeg" in url.lower():
        ext = ".jpeg"
    cache_path = IMAGE_CACHE / f"bug{bug_id}_{index}_{url_hash}{ext}"

    if cache_path.exists():
        logger.debug("[images] Using cached: %s", cache_path.name)
        return cache_path

    try:
        resp = requests.get(
            url,
            headers={"token": token},
            timeout=30,
        )
        if resp.status_code == 200 and len(resp.content) > 100:
            # Verify it's actually an image, not an HTML login page
            header = resp.content[:4]
            if header[:4] == b'<!DO' or header[:4] == b'<htm' or header[:5] == b'<HTML':
                logger.warning("[images] Skipping non-image (HTML page) for Bug #%s", bug_id)
                return None
            with open(cache_path, "wb") as f:
                f.write(resp.content)
            logger.info("[images] Downloaded Bug #%s image %d: %s (%d bytes)",
                        bug_id, index, cache_path.name, len(resp.content))
            return cache_path
        else:
            logger.warning("[images] Failed to download %s: status=%d, len=%d",
                           url[:60], resp.status_code, len(resp.content))
    except Exception as e:
        logger.warning("[images] Download error for %s: %s", url[:60], e)

    return None


def get_bug_images(bug_id: str, bug_steps_html: str = "") -> list[Path]:
    """
    Extract and download all images from a bug's description.
    If bug_steps_html is empty, fetch from zentao API first.
    Returns list of local image paths.
    """
    if not bug_steps_html:
        # Fetch from API
        bug_steps_html = _fetch_bug_steps(bug_id)

    urls = extract_image_urls(bug_steps_html)
    if not urls:
        return []

    images = []
    for i, url in enumerate(urls[:5]):  # Max 5 images per bug
        path = download_image(url, bug_id, i)
        if path:
            images.append(path)

    return images


def _fetch_bug_steps(bug_id: str) -> str:
    """Fetch bug details from zentao API to get the steps HTML."""
    token = _get_token()
    if not token:
        return ""
    try:
        resp = requests.get(
            f"{ZENTAO_URL}/api.php/v1/bugs/{bug_id}",
            headers={"token": token},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("steps", "")
    except Exception as e:
        logger.warning("[images] API fetch failed for Bug #%s: %s", bug_id, e)
    return ""


def describe_images(images: list[Path], bug_id: str) -> str:
    """Generate a text description of downloaded images for LLM prompts."""
    if not images:
        return ""
    lines = [f"\n## Bug #{bug_id} 截图（{len(images)} 张）"]
    for i, path in enumerate(images):
        size_kb = path.stat().st_size // 1024
        lines.append(f"  [{i+1}] {path.name} ({size_kb}KB)")
        # Try basic OCR hint
        try:
            from PIL import Image
            img = Image.open(path)
            lines.append(f"      尺寸: {img.size[0]}x{img.size[1]}")
        except Exception:
            pass
    lines.append("  ⚠️ 请仔细查看以上截图中的报错信息，可能包含关键错误堆栈。")
    return "\n".join(lines)
