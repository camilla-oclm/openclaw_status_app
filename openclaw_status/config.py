"""
Central config: paths, constants, models, .env loading.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Project root ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

# ── .env ────────────────────────────────────────────────────────────────────
load_dotenv(ROOT / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    print("⚠ WARNING: OPENROUTER_API_KEY not set. LLM calls will fail.", file=sys.stderr)

# Optional GitHub token. When set, GitHub issues/releases are read via the direct
# GitHub API (richer data — reactions, body — in one query, and more robust).
# When absent, the collector falls back to Composio. Needs only public read:
# fine-grained PAT with Issues:Read + Metadata:Read, or a classic token with no scopes.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# ── Repository ──────────────────────────────────────────────────────────────
REPO_OWNER = "openclaw"
REPO_NAME = "openclaw"
NPM_PACKAGE = "openclaw"
REPO_PATH = f"{REPO_OWNER}-{REPO_NAME}"

# ── Model config ────────────────────────────────────────────────────────────
PRIMARY_MODEL = "deepseek/deepseek-v4-flash"
PRIMARY_REASONING = {"effort": "high", "exclude": False}
VALIDATOR_MODEL = "openrouter/owl-alpha"
VALIDATOR_REASONING = None  # owl-alpha doesn't support reasoning

# Fallback models (used if primary fails, in order)
FALLBACK_MODELS = [
    {"model": "xiaomi-coding/mimo-v2.5", "reasoning": None},
    {"model": "ollama/kimi-k2.6:cloud", "reasoning": None},
    {"model": "openrouter/deepseek/deepseek-v4-flash", "reasoning": None},
]

# ── Data files ──────────────────────────────────────────────────────────────
RAW_DATA_FILE = DATA_DIR / "raw-data.json"
ASSESSMENT_FILE = DATA_DIR / "assessment.json"
USAGE_LOG_FILE = DATA_DIR / "usage.json"
HISTORY_FILE = DATA_DIR / "history.json"
FINDINGS_HTML = DATA_DIR / "findings.html"
RUN_LOG_FILE = DATA_DIR / "run-log.json"

# ── Composio ────────────────────────────────────────────────────────────────
COMPOSIO_PATH = os.path.expanduser("~/.composio")
COMPOSIO_ENV = {**os.environ, "PATH": f"{COMPOSIO_PATH}:{os.environ.get('PATH', '')}"}

# ── API endpoints ───────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# ── Frontend ────────────────────────────────────────────────────────────────
# The renderer injects pipeline data into TEMPLATE_FILE via the
# <script id="assessment-data"> JSON contract and writes the public page to OUTPUT_HTML.
TEMPLATE_FILE = WEB_DIR / "template.html"
OUTPUT_HTML = WEB_DIR / "index.html"
