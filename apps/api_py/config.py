from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
UPLOADS_DIR = ROOT_DIR / "storage" / "uploads"
STORE_FILE = DATA_DIR / "app-store.json"
WEB_DIR = ROOT_DIR / "apps" / "web" / "static"
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:3001")

TEXT_EXTENSIONS = {
    ".env",
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".js",
    ".ts",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
}


def _walk_text_files(start_dir: Path, depth: int = 0) -> list[Path]:
    if not start_dir.exists() or depth > 4:
        return []

    results: list[Path] = []
    for entry in start_dir.iterdir():
        if entry.is_dir():
            results.extend(_walk_text_files(entry, depth + 1))
        elif entry.suffix.lower() in TEXT_EXTENSIONS or entry.name == ".env":
            results.append(entry)
    return results


def _discover_secret(pattern: str, directories: list[Path]) -> str | None:
    regex = re.compile(pattern)
    for directory in directories:
        for file_path in _walk_text_files(directory):
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = file_path.read_text(encoding="gbk")
                except Exception:
                    continue
            except Exception:
                continue

            match = regex.search(content)
            if match:
                return match.group(0)

    return None


def _find_candidate_directories(*keywords: str) -> list[Path]:
    keywords_lower = [keyword.lower() for keyword in keywords]
    candidates: list[Path] = []

    # Match directories by normalized names so localized folder names still work.
    for entry in ROOT_DIR.iterdir():
        if not entry.is_dir():
            continue

        normalized_name = entry.name.lower()
        if any(keyword in normalized_name for keyword in keywords_lower):
            candidates.append(entry)

    return candidates


def _discover_glm_key() -> str | None:
    candidates = _find_candidate_directories("langgraph", "mem0")
    return _discover_secret(r"\b[a-f0-9]{32}\.[A-Za-z0-9]+\b", candidates)


def _discover_mem0_key() -> str | None:
    return _discover_secret(r"\bm0-[A-Za-z0-9]+\b", _find_candidate_directories("mem0"))


@dataclass(frozen=True)
class Settings:
    port: int = int(os.getenv("PORT", "3000"))
    app_url: str = os.getenv("APP_URL", "http://localhost:3000")
    auth_service_url: str = AUTH_SERVICE_URL
    internal_service_secret: str = os.getenv("INTERNAL_SERVICE_SECRET", "dev-internal-secret")
    glm_model: str = os.getenv("GLM_MODEL", "glm-4.7")
    glm_base_url: str = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    glm_api_key: str | None = os.getenv("GLM_API_KEY") or _discover_glm_key()
    mem0_api_key: str | None = os.getenv("MEM0_API_KEY") or _discover_mem0_key()
    store_file: Path = STORE_FILE
    uploads_dir: Path = UPLOADS_DIR
    web_dir: Path = WEB_DIR
    root_dir: Path = ROOT_DIR


settings = Settings()
