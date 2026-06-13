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

# GitHub token — REQUIRED. All GitHub data (issues + releases) is read via the
# GitHub API with this token. Needs only public read: a fine-grained PAT with
# Issues:Read + Metadata:Read, or a classic token with no scopes.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("⚠ WARNING: GITHUB_TOKEN not set. GitHub collection will fail.", file=sys.stderr)

# ── Repository ──────────────────────────────────────────────────────────────
REPO_OWNER = "openclaw"
REPO_NAME = "openclaw"
NPM_PACKAGE = "openclaw"
REPO_PATH = f"{REPO_OWNER}-{REPO_NAME}"

# ── Model config ────────────────────────────────────────────────────────────
# All models are served through OpenRouter. The analyst role uses deepseek-v4-pro
# (the strong sibling of the flash model this project was built on — same prompt,
# reasoning param, and JSON behaviour, a clear quality step up, still ~$0.009/run).
PRIMARY_MODEL = "deepseek/deepseek-v4-pro"
PRIMARY_REASONING = {"effort": "high", "exclude": False}
VALIDATOR_MODEL = "openrouter/owl-alpha"
VALIDATOR_REASONING = None  # owl-alpha doesn't support reasoning

# Fallback (used if the primary fails). A single cross-provider choice: qwen3.7-plus
# is a different vendor entirely, so a deepseek outage doesn't take the run down.
# IDs are real OpenRouter slugs (provider/model) — a wrong slug returns HTTP 400
# and burns a retry, so keep them in sync with https://openrouter.ai/api/v1/models.
FALLBACK_MODELS = [
    {"model": "qwen/qwen3.7-plus", "reasoning": {"effort": "high", "exclude": False}},
]

# Assessment output budget. The analyst/refine steps emit a full JSON document
# (thesis + evidence + one known_issues entry per issue + changes), which blows
# past the 4k default and truncates mid-JSON → "Failed to parse JSON." Crucially,
# OpenRouter counts reasoning tokens against this cap too: a high-effort run burns
# ~4–6k tokens *just thinking* before any JSON, so the budget must cover reasoning
# + the full document. 16k clears both with margin (deepseek-v4-pro allows 384k
# output, qwen3.7-plus 65k). The validator's output stays small → keeps the default.
ASSESSMENT_MAX_TOKENS = 16000

# Cap on how many issues are fed into the LLM prompt. The collector persists the
# full ranked set to raw-data.json; only the top-N by rank go to the model, which
# bounds both the input context (~1k chars/issue) and the known_issues output.
MAX_ISSUES_IN_CONTEXT = 30

# ── Data files ──────────────────────────────────────────────────────────────
RAW_DATA_FILE = DATA_DIR / "raw-data.json"
ASSESSMENT_FILE = DATA_DIR / "assessment.json"
USAGE_LOG_FILE = DATA_DIR / "usage.json"
HISTORY_FILE = DATA_DIR / "history.json"

# ── API endpoints ───────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_API_URL = "https://api.github.com"
GITHUB_RAW_URL = "https://raw.githubusercontent.com"

# ── Frontend ────────────────────────────────────────────────────────────────
# The renderer injects pipeline data into TEMPLATE_FILE via the
# <script id="assessment-data"> JSON contract and writes the public page to OUTPUT_HTML.
TEMPLATE_FILE = WEB_DIR / "template.html"
OUTPUT_HTML = WEB_DIR / "index.html"
