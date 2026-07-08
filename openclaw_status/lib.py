"""
Shared utilities: retries, sanitization, OpenRouter API, file I/O, usage/cost
tracking, pipeline lock, run log, and the pipeline timer.
"""

import fcntl
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
except ImportError:
    ThreadPoolExecutor = None
    as_completed = None

from openclaw_status import config


# ═══════════════════════════════════════════════════════════════════════════
#  Retry helpers
# ═══════════════════════════════════════════════════════════════════════════

def _retry(func, *args, retries=None, **kwargs):
    """Call func with retries and exponential backoff. Returns (result, attempts).
    `retries` defaults to config.MAX_RETRIES (resolved at call time)."""
    if retries is None:
        retries = config.MAX_RETRIES
    last_err = None
    for attempt in range(retries + 1):
        try:
            result = func(*args, **kwargs)
            return result, attempt + 1
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = config.RETRY_BACKOFF[min(attempt, len(config.RETRY_BACKOFF) - 1)]
                print(f"  ↻ Retry {attempt+1}/{retries} in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
    raise last_err


def _call_with_wallclock(func, wall_clock: float):
    """Run `func` in a daemon thread, abandoning it if it runs longer than `wall_clock`
    seconds. Necessary because urllib's socket `timeout` is a per-read idle timeout, not
    a total deadline: a model that trickles tokens slowly resets it on every byte, so a
    single call can otherwise block indefinitely (it once hung a run ~17 min until
    systemd SIGKILLed it). The abandoned thread keeps running but is a daemon, so it
    never blocks process exit. Re-raises whatever `func` raised; raises TimeoutError if
    the budget is exceeded (or was already exhausted)."""
    if wall_clock <= 0:
        raise TimeoutError("wall-clock budget already exhausted")
    box = {}

    def _target():
        try:
            box["result"] = func()
        except BaseException as e:  # propagate the real error to the caller
            box["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(wall_clock)
    if t.is_alive():
        raise TimeoutError(f"call exceeded wall-clock budget ({wall_clock:.0f}s)")
    if "error" in box:
        raise box["error"]
    return box.get("result")


# ═══════════════════════════════════════════════════════════════════════════
#  OpenRouter
# ═══════════════════════════════════════════════════════════════════════════

def openrouter_call(
    model_id: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int = 4000,
    reasoning: dict = None,
    temperature: float = 0.1,
    retries: int | None = None,
    deadline: float | None = None,
) -> dict:
    """Single call to OpenRouter. Returns {success, parsed, model, usage, error?}.

    `deadline` (an absolute time.time() epoch, optional) hard-bounds each attempt to the
    time remaining before it, so a trickling/hung response can't blow past the run's
    wall-clock budget (see _call_with_wallclock). Once the deadline passes, the remaining
    retries fail fast and the call returns success=False — callers degrade from there."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    payload_dict = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning:
        payload_dict["reasoning"] = reasoning
        payload_dict["include_reasoning"] = True

    payload = json.dumps(payload_dict).encode()

    def _call():
        req = urllib.request.Request(
            config.OPENROUTER_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://clawstat.us",
            },
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        latency = int((time.time() - start) * 1000)

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        parsed = extract_json(content)
        return {
            "success": True,
            "parsed": parsed,
            "model": model_id,
            "usage": {
                "tokens_in": usage.get("prompt_tokens", 0),
                "tokens_out": usage.get("completion_tokens", 0),
                "cost_usd": usage.get("cost", 0),
                "latency_ms": latency,
            },
        }

    def _attempt():
        # Bound each attempt to the time left in the pipeline budget. urllib's own
        # timeout=180 is a per-read idle timeout, not a total deadline, so it can't
        # catch a slow-trickle response on its own — _call_with_wallclock is the hard cap.
        if deadline is None:
            return _call()
        return _call_with_wallclock(_call, deadline - time.time())

    try:
        result, attempts = _retry(_attempt, retries=retries)
        if attempts > 1:
            print(f"  ✓ {model_id} succeeded after {attempts} attempts", file=sys.stderr)
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.readable() else ""
        return {"success": False, "model": model_id, "error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"success": False, "model": model_id, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
#  JSON helpers
# ═══════════════════════════════════════════════════════════════════════════

def extract_json(content: str) -> dict:
    """Robust JSON extraction from an LLM response.

    Handles: markdown fences, reasoning tokens, trailing text, nested JSON.
    Strategy: find the outermost { ... } block and parse it.
    """
    text = content.strip()

    # Remove opening fence (including ```json etc.)
    if text.startswith("```"):
        inner_start = text.find("\n")
        text = text[inner_start + 1:] if inner_start != -1 else text[3:]

    # Remove closing fence (and any trailing text)
    last_fence = text.rfind("```")
    if last_fence != -1:
        trailing = text[last_fence:].strip()
        if trailing == "```" or trailing.startswith("```"):
            text = text[:last_fence].strip()

    text = text.strip()

    # Strategy 1: Direct parse (cleanest case)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find outermost { ... } by brace matching
    # Handles reasoning tokens or commentary prepended/appended. Braces inside
    # string values are skipped (with escape handling) so a `}` in a string can't
    # close the object early.
    start = text.find("{")
    if start != -1:
        depth = 0
        end = -1
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end != -1:
            candidate = text[start:end]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # Strategy 3: Strip known prefixes (e.g., "Here's the assessment:")
    for prefix in ("Here is", "Here's", "The assessment", "Based on", "After analyzing"):
        idx = text.lower().find(prefix.lower())
        if idx != -1:
            rest = text[idx:]
            brace_start = rest.find("{")
            if brace_start != -1:
                sub = rest[brace_start:]
                try:
                    return json.loads(sub)
                except json.JSONDecodeError:
                    pass

    return {"error": "Failed to parse JSON", "raw": text[:1000]}


def load_json(path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_json_or(path, default):
    """load_json, or `default` when the file is missing/unreadable/corrupt — the
    standard read for optional state files (history/timeline/usage/ledger)."""
    try:
        return load_json(path)
    except Exception:
        return default


def atomic_write_text(path, text: str, mode: int | None = None):
    """Write text atomically: temp file in the same directory, then os.replace().
    A crash/kill mid-write can't leave a half-written file — readers see either the
    old content or the complete new one. The data is fsync'd before the rename and the
    parent directory after, so the guarantee holds under an ungraceful host crash /
    power loss too (not just a graceful SIGKILL, where the page cache survives). `mode`
    (e.g. 0o644) is chmod'd after the replace, best-effort, for a separate server user."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())               # persist the data BEFORE the rename
        os.replace(tmp, str(path))
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)   # then persist the rename itself
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass                               # best-effort (e.g. a FS without dir fsync)
        if mode is not None:
            try:
                os.chmod(path, mode)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_json(path, data):
    """Write JSON atomically (see atomic_write_text) — a crash/kill mid-write must
    never leave a half-written (corrupt) state file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════════════
#  Sanitization
# ═══════════════════════════════════════════════════════════════════════════

INJECTION_PATTERNS = [
    # English
    r"(?i)ignore\s+(all\s+)?previous\s+instructions",
    r"(?i)you\s+are\s+now\s+",
    r"(?i)\[INST\]",
    r"(?i)\[/INST\]",
    r"(?i)system:\s*",
    r"(?i)ignore\s+above",
    r"(?i)disregard\s+(all\s+)?prior",
    r"(?i)new\s+instructions:",
    r"(?i)override\s+instructions",
    r"(?i)forget\s+(all\s+)?(previous|prior|earlier)",
    r"(?i)act\s+as\s+if\s+you\s+(are|were)",
    r"(?i)from\s+now\s+on",
    r"(?i)pretend\s+(you|that)",
    r"(?i)roleplay\s+as",
    # Spanish
    r"(?i)ignora\s+(todas\s+)?(las\s+)?instrucciones\s+anteriores",
    r"(?i)olvida\s+(todo\s+)?(lo\s+)?anterior",
    r"(?i)ahora\s+eres\s+",
    # Chinese / Japanese / Korean (CJK)
    r"(?i)忽略(之前|以上|前面)(的)?(所有)?(指令|指示|说明)",
    r"(?i)あなたは(今|これから)",
    # HTML entity obfuscation
    r"&lt;|&gt;|&amp;|&#\d+;|&#x[0-9a-fA-F]+;",
]

# Invisible / obfuscation characters. Removed ENTIRELY (not marked) and BEFORE
# instruction matching, so they can't be spliced into keywords like
# "ig\u200bnore previous instructions" to defeat the patterns above.
OBFUSCATION_PATTERNS = [
    r"[\u200b\u200c\u200d\ufeff\u00ad]",  # zero-width chars + soft hyphen
]


def _strip_injection_patterns(text: str) -> str:
    """Remove obfuscation chars, then mark known injection patterns as [STRIPPED]."""
    for pattern in OBFUSCATION_PATTERNS:
        text = re.sub(pattern, "", text)
    for pattern in INJECTION_PATTERNS:
        text = re.sub(pattern, "[STRIPPED]", text)
    return text


def sanitize(text: str, max_len: int = 2000) -> str:
    """Strip prompt injection patterns and HTML tags, then truncate."""
    if not text:
        return ""
    # Strip HTML tags first
    text = re.sub(r"<[^>]+>", "", text)
    # Strip injection patterns (all languages)
    text = _strip_injection_patterns(text)
    if len(text) > max_len:
        text = text[:max_len] + "... [TRUNCATED]"
    return text.strip()


def sanitize_for_html(text: str) -> str:
    """HTML-escape text for safe embedding in HTML context.
    Use this when injecting LLM output into HTML templates.
    """
    if not text:
        return ""
    import html as html_mod
    text = html_mod.escape(str(text))
    # Extra: escape </script> variants even after html.escape
    text = text.replace("</script", "&lt;/script").replace("<!--", "&lt;!--")
    return text


_MD_LINK_RE = re.compile(r"!?\[([^\]]+)\]\((?:https?://[^)\s]+)\)")


def strip_md_links(text: str) -> str:
    """Unwrap markdown links (and images) to their text: "[#82909](https://…)" → "#82909".

    Changelog bullets arrive with raw markdown link syntax, but the frontend builds its DOM
    from plain text (no markdown pipeline), so a wrapped link would render literally as
    "[x](url)". Unwrapping keeps the text clean while the page's linkify() still turns bare
    #N refs into real links.
    """
    return _MD_LINK_RE.sub(r"\1", text or "")


# ═══════════════════════════════════════════════════════════════════════════
#  Usage logging
# ═══════════════════════════════════════════════════════════════════════════

def log_usage(model_id: str, usage: dict, success: bool):
    now = datetime.now(timezone.utc).isoformat()
    log = load_json_or(config.USAGE_LOG_FILE, [])
    log.append({"timestamp": now, "model": model_id, "success": success, **usage})
    if len(log) > 1000:
        log = log[-1000:]
    save_json(config.USAGE_LOG_FILE, log)


# ═══════════════════════════════════════════════════════════════════════════
#  Cost tracking  (thresholds live in config: DAILY/MONTHLY_COST_LIMIT)
# ═══════════════════════════════════════════════════════════════════════════


def notify(text: str) -> bool:
    """Best-effort push of an alert to config.ALERT_WEBHOOK_URL. No-op if unset;
    never raises — an alert must not crash a run. Returns True only if delivered.

    Discord incoming webhooks expect {"content": ...}; Slack (and most others)
    expect {"text": ...}. We pick the key the target understands from the URL."""
    url = config.ALERT_WEBHOOK_URL
    if not url:
        return False
    key = "content" if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url else "text"
    try:
        data = json.dumps({key: text}).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "openclaw-status"},
        )
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception as e:
        print(f"  ⚠ Alert webhook failed: {e}", file=sys.stderr)
        return False


def check_cost_thresholds():
    """Check daily and monthly costs against limits. Returns (daily_total, monthly_total, alerts).

    Money accounting counts EVERY logged ``cost_usd`` regardless of the ``success`` flag:
    OpenRouter bills a call whose HTTP succeeded but whose JSON was unparseable (logged
    ``success=False``), so excluding those would let repeated parse-error runs spend real
    money invisibly to this budget gate — the exact runaway the gate exists to stop. The
    ``success`` flag is for quality/value accounting, not for money.
    """
    alerts = []
    now = datetime.now(timezone.utc)

    log = load_json_or(config.USAGE_LOG_FILE, [])

    # Daily total
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    daily = sum(
        entry.get("cost_usd", 0) for entry in log
        if entry.get("timestamp", "") >= today_start
    )
    if daily > config.DAILY_COST_LIMIT:
        alerts.append(f"Daily cost ${daily:.4f} exceeds ${config.DAILY_COST_LIMIT} limit")

    # Monthly total
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    monthly = sum(
        entry.get("cost_usd", 0) for entry in log
        if entry.get("timestamp", "") >= month_start
    )
    if monthly > config.MONTHLY_COST_LIMIT:
        alerts.append(f"Monthly cost ${monthly:.4f} exceeds ${config.MONTHLY_COST_LIMIT} limit")

    return daily, monthly, alerts


# ═══════════════════════════════════════════════════════════════════════════
#  Parallel execution helper
# ═══════════════════════════════════════════════════════════════════════════

def parallel_fetch(func, items, max_workers=4):
    """Run func(item) in parallel for each item. Returns a list of results ALIGNED with
    `items` by position — results[i] is func(items[i]). Items that fail get None (no crash).

    Position-keyed, NOT keyed by item value: a duplicate item is a distinct call whose
    result must not collapse onto another's. Callers that count per-occurrence outcomes
    (e.g. scout coverage: "one of two identical queries failed") depend on this — a
    value-keyed dict would silently discard one real result and miscount coverage.
    """
    items = list(items)
    results = [None] * len(items)
    if ThreadPoolExecutor is None or len(items) <= 1:
        for i, item in enumerate(items):
            try:
                results[i] = func(item)
            except Exception:
                results[i] = None
        return results

    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
        futures = {executor.submit(func, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception:
                results[i] = None
    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Misc helpers
# ═══════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_rec(rec: str) -> str:
    """Map the retired 🔄 "wait for next release" verdict onto ⏸️ "skip this version"
    (3-verdict rubric: ✅/⚠️/⏸️). All current verdicts pass through. Keeps old 🔄
    history entries / any stray model output coherent. The shared mapping — BOTH
    normalizer call sites stay (agent pre-validation + render for history/timeline)."""
    return "⏸️" if rec == "🔄" else rec


def version_from_release(release: dict | None) -> str:
    if not release:
        return ""
    return release.get("tag", "").lstrip("v")


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline Lock File (Idempotency)
# ═══════════════════════════════════════════════════════════════════════════

_pipeline_lock_fds: list = []


def acquire_pipeline_lock(lock_path: Path = None) -> bool:
    """Acquire a pipeline lock using a PID file with flock.

    Creates a lock file at data/.pipeline.lock. If the lock is held by a
    process that is still alive, returns False. Otherwise acquires the lock.
    Uses fcntl.flock for atomic locking.

    Args:
        lock_path: path to lock file (default: config.DATA_DIR / '.pipeline.lock')

    Returns:
        True if lock acquired, False if another live process holds it.
    """
    if lock_path is None:
        lock_path = config.DATA_DIR / ".pipeline.lock"

    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Open WITHOUT truncating (O_RDWR|O_CREAT, not "w") so we can still read the
        # current holder's PID after a failed flock. flock is the source of truth for
        # mutual exclusion: the kernel auto-releases it when a holder dies, so a
        # successful flock here means any previous (even crashed) holder is gone.
        fd = os.fdopen(os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644), "r+")
    except OSError as e:
        print(f"  ⚠ Lock acquisition error: {e}", file=sys.stderr)
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by a live process — report its PID (best-effort) and back off.
        try:
            fd.seek(0)
            holder = fd.read().strip()
        except OSError:
            holder = ""
        fd.close()
        print(f"  ⚠ Pipeline locked by PID {holder or '?'} (still running)", file=sys.stderr)
        return False

    # Acquired (fresh, or reclaimed from a dead holder) — record our PID.
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except OSError as e:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        print(f"  ⚠ Lock acquisition error: {e}", file=sys.stderr)
        return False

    # Keep fd open for the process lifetime — closing it releases the flock.
    _pipeline_lock_fds.append(fd)
    print(f"  🔒 Pipeline locked (PID {os.getpid()})")
    return True


def release_pipeline_lock(lock_path: Path = None):
    """Release the pipeline lock (flock LOCK_UN + close).

    Deliberately does NOT unlink the lock file. flock alone provides mutual exclusion and the
    kernel auto-releases it on process death, so keeping a PERSISTENT lock file is correct.
    Unlinking after LOCK_UN opened the classic flock-over-unlink race — a second acquirer
    holding the just-released inode while a third O_CREATs a fresh one, letting two pipelines
    run at once and double-spend (D21). `lock_path` is accepted for signature compatibility."""
    for fd in _pipeline_lock_fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass
    _pipeline_lock_fds.clear()
    print("  🔓 Pipeline lock released")


# ═══════════════════════════════════════════════════════════════════════════
#  Run Log / Audit Trail
# ═══════════════════════════════════════════════════════════════════════════

class RunLog:
    """Track metadata for a single pipeline run.

    Persists to data/run-log.json (keeps last 100 entries).
    """

    MAX_ENTRIES = 100

    def __init__(self, trigger_type: str = "manual"):
        self.run_id = str(uuid.uuid4())
        self.trigger_type = trigger_type
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.end_time: str = ""
        self.duration_s: float = 0
        self.source_status: dict = {}
        self.cost_usd: float = 0
        self.model_used: str = ""
        self.recommendation: str = ""
        self.validation_errors: list[str] = []
        self.pipeline_aborted: bool = False
        self.abort_reason: str = ""
        self._start_ts = time.time()

    def update(self, **kwargs):
        """Update run log fields from keyword arguments."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def finish(self):
        """Mark the run as complete and compute duration."""
        self.end_time = datetime.now(timezone.utc).isoformat()
        self.duration_s = round(time.time() - self._start_ts, 2)

    def save(self):
        """Save this run to the log file, keeping the last MAX_ENTRIES."""
        log_path = self._get_log_path()
        entries = []
        if log_path.exists():
            try:
                entries = load_json(log_path)
            except Exception:
                entries = []

        entry = {
            "run_id": self.run_id,
            "trigger_type": self.trigger_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_s": self.duration_s,
            "source_status": self.source_status,
            "cost_usd": self.cost_usd,
            "model_used": self.model_used,
            "recommendation": self.recommendation,
            "validation_errors": self.validation_errors,
            "pipeline_aborted": self.pipeline_aborted,
            "abort_reason": self.abort_reason,
        }
        entries.append(entry)
        if len(entries) > self.MAX_ENTRIES:
            entries = entries[-self.MAX_ENTRIES:]
        save_json(log_path, entries)

    def _get_log_path(self) -> Path:
        return config.DATA_DIR / "run-log.json"

    def __repr__(self):
        return f"<RunLog {self.run_id[:8]} aborted={self.pipeline_aborted}>"


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline Timer (Backpressure / Timeout)
# ═══════════════════════════════════════════════════════════════════════════

class PipelineTimer:
    """Context manager that tracks elapsed pipeline time and sets a timeout flag.

    Usage:
        with PipelineTimer(timeout=900) as timer:
            # do work
            if timer.exceeded:
                # abort gracefully
    """

    def __init__(self, timeout: float = 900.0):
        self.timeout = timeout
        self.start_time: float = 0
        self.exceeded: bool = False

    def __enter__(self):
        self.start_time = time.time()
        self.exceeded = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False  # Don't suppress exceptions

    @property
    def elapsed(self) -> float:
        """Elapsed seconds since timer started."""
        return time.time() - self.start_time

    @property
    def remaining(self) -> float:
        """Seconds remaining before timeout. Negative if exceeded."""
        return self.timeout - self.elapsed

    def check(self) -> bool:
        """Check if timeout has been exceeded. Sets the exceeded flag and returns it."""
        if not self.exceeded and self.elapsed >= self.timeout:
            self.exceeded = True
            print(f"  ⏰ Pipeline timeout exceeded ({self.timeout:.0f}s)", file=sys.stderr)
        return self.exceeded
