"""
Shared utilities: sanitization, Composio runner, OpenRouter API, file I/O.
"""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import threading
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

MAX_RETRIES = 2
RETRY_BACKOFF = [1.0, 3.0]  # seconds


def _retry(func, *args, retries=MAX_RETRIES, **kwargs):
    """Call func with retries and exponential backoff. Returns (result, attempts)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            result = func(*args, **kwargs)
            return result, attempt + 1
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                print(f"  ↻ Retry {attempt+1}/{retries} in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
    raise last_err


# ═══════════════════════════════════════════════════════════════════════════
#  Composio
# ═══════════════════════════════════════════════════════════════════════════

def composio(tool_slug: str, params: dict, timeout: int = 30) -> dict | None:
    """Execute a Composio tool, returning parsed JSON output. Returns None on failure."""
    cmd = ["composio", "execute", tool_slug, "-d", json.dumps(params)]

    def _run():
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=config.COMPOSIO_ENV
        )
        if result.returncode != 0:
            print(f"  ⚠ {tool_slug} failed (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
            return None

        output = result.stdout
        # Find the LAST complete JSON object (skip log lines, banners)
        # Use brace-matching instead of naive rfind to handle nested JSON
        json_start = output.rfind("{")
        if json_start == -1:
            print(f"  ⚠ {tool_slug}: no JSON in output", file=sys.stderr)
            return None

        depth = 0
        json_end = -1
        for i in range(json_start, len(output)):
            if output[i] == '{':
                depth += 1
            elif output[i] == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break

        if json_end == -1:
            print(f"  ⚠ {tool_slug}: no complete JSON in output", file=sys.stderr)
            return None

        data = json.loads(output[json_start:json_end])
        if "successful" in data:
            if not data.get("successful"):
                print(f"  ⚠ {tool_slug}: unsuccessful: {data.get('error','unknown')}", file=sys.stderr)
                return None
            return data.get("data", data)
        return data

    try:
        result, attempts = _retry(_run)
        if attempts > 1:
            print(f"  ✓ {tool_slug} succeeded after {attempts} attempts", file=sys.stderr)
        return result
    except subprocess.TimeoutExpired:
        print(f"  ⚠ {tool_slug} timed out after {timeout}s", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠ {tool_slug}: JSON parse error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ⚠ {tool_slug}: unexpected error: {e}", file=sys.stderr)
        return None


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
    retries: int = MAX_RETRIES,
) -> dict:
    """Single call to OpenRouter. Returns {success, parsed, model, usage, error?}."""
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
                "HTTP-Referer": "https://openclawstatus.io",
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

    try:
        result, attempts = _retry(_call, retries=retries)
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
    # Handles reasoning tokens or commentary prepended/appended
    start = text.find("{")
    if start != -1:
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
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


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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


# ═══════════════════════════════════════════════════════════════════════════
#  Usage logging
# ═══════════════════════════════════════════════════════════════════════════

def log_usage(model_id: str, usage: dict, success: bool):
    now = datetime.now(timezone.utc).isoformat()
    log = []
    if config.USAGE_LOG_FILE.exists():
        try:
            log = load_json(config.USAGE_LOG_FILE)
        except Exception:
            log = []
    log.append({"timestamp": now, "model": model_id, "success": success, **usage})
    if len(log) > 1000:
        log = log[-1000:]
    save_json(config.USAGE_LOG_FILE, log)


# ═══════════════════════════════════════════════════════════════════════════
#  Cost tracking
# ═══════════════════════════════════════════════════════════════════════════

DAILY_COST_LIMIT = 2.0    # USD
MONTHLY_COST_LIMIT = 30.0  # USD


def check_cost_thresholds():
    """Check daily and monthly costs against limits. Returns (daily_total, monthly_total, alerts)."""
    alerts = []
    now = datetime.now(timezone.utc)

    log = []
    if config.USAGE_LOG_FILE.exists():
        try:
            log = load_json(config.USAGE_LOG_FILE)
        except Exception:
            pass

    # Daily total
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    daily = sum(
        entry.get("cost_usd", 0) for entry in log
        if entry.get("timestamp", "") >= today_start and entry.get("success", False)
    )
    if daily > DAILY_COST_LIMIT:
        alerts.append(f"Daily cost ${daily:.4f} exceeds ${DAILY_COST_LIMIT} limit")

    # Monthly total
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    monthly = sum(
        entry.get("cost_usd", 0) for entry in log
        if entry.get("timestamp", "") >= month_start and entry.get("success", False)
    )
    if monthly > MONTHLY_COST_LIMIT:
        alerts.append(f"Monthly cost ${monthly:.4f} exceeds ${MONTHLY_COST_LIMIT} limit")

    return daily, monthly, alerts


# ═══════════════════════════════════════════════════════════════════════════
#  Parallel execution helper
# ═══════════════════════════════════════════════════════════════════════════

def parallel_fetch(func, items, max_workers=4):
    """Run func(item) in parallel for each item. Returns {item: result} dict.
    Items that fail get None as their result (no crash).
    """
    results = {}
    if ThreadPoolExecutor is None or len(items) <= 1:
        for item in items:
            try:
                results[item] = func(item)
            except Exception:
                results[item] = None
        return results

    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
        futures = {executor.submit(func, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item] = future.result()
            except Exception:
                results[item] = None
    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Firecrawl output parser
# ═══════════════════════════════════════════════════════════════════════════

def parse_firecrawl_markdown(data: dict) -> str:
    """Extract markdown from a Firecrawl scrape result."""
    output_path = data.get("outputFilePath", "")
    if not output_path or not os.path.exists(output_path):
        return ""
    try:
        inner = load_json(output_path)
        # Navigate nested structure robustly: data -> data -> data, or data -> markdown
        if isinstance(inner, dict):
            for _ in range(3):  # max 3 levels of nesting
                if isinstance(inner.get("data"), dict):
                    inner = inner["data"]
                else:
                    break
            # Look for markdown at current or one level deeper
            if "markdown" in inner and isinstance(inner["markdown"], str):
                return inner["markdown"] or ""
            if isinstance(inner.get("data"), dict) and "markdown" in inner["data"]:
                return inner["data"]["markdown"] or ""
            # Last resort: search recursively for first "markdown" key
            found = _find_markdown_key(inner)
            if found:
                return found
        return ""
    except Exception as e:
        print(f"  ⚠ Failed to read Firecrawl output: {e}", file=sys.stderr)
        return ""


def _find_markdown_key(obj, max_depth=5):
    """Recursively search for a 'markdown' key in nested dicts."""
    if max_depth <= 0 or not isinstance(obj, dict):
        return ""
    if "markdown" in obj and isinstance(obj["markdown"], str):
        return obj["markdown"]
    for v in obj.values():
        if isinstance(v, dict):
            found = _find_markdown_key(v, max_depth - 1)
            if found:
                return found
    return ""


# ═══════════════════════════════════════════════════════════════════════════
#  Misc helpers
# ═══════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Could not acquire — check if PID is alive
            try:
                with open(lock_path, "r") as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)  # Check if alive
                fd.close()
                print(f"  ⚠ Pipeline locked by PID {old_pid} (still running)", file=sys.stderr)
                return False
            except (ValueError, ProcessLookupError, FileNotFoundError):
                # PID dead or unreadable — steal the lock
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write our PID
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
        # Keep fd open — closing releases the flock on some systems
        _pipeline_lock_fds.append(fd)
        print(f"  🔒 Pipeline locked (PID {os.getpid()})")
        return True
    except Exception as e:
        print(f"  ⚠ Lock acquisition error: {e}", file=sys.stderr)
        return False


def release_pipeline_lock(lock_path: Path = None):
    """Release the pipeline lock and remove the lock file."""
    if lock_path is None:
        lock_path = config.DATA_DIR / ".pipeline.lock"

    for fd in _pipeline_lock_fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass
    _pipeline_lock_fds.clear()

    try:
        lock_path.unlink(missing_ok=True)
        print(f"  🔓 Pipeline lock released")
    except Exception:
        pass


def check_pipeline_locked(lock_path: Path = None) -> bool:
    """Check if a pipeline lock is currently held by a live process.

    Returns:
        True if locked by a live process, False otherwise.
    """
    if lock_path is None:
        lock_path = config.DATA_DIR / ".pipeline.lock"

    if not lock_path.exists():
        return False
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError):
        return False


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
#  Staleness Check
# ═══════════════════════════════════════════════════════════════════════════

def check_data_staleness(raw: dict, assessment_path: Path = None) -> bool:
    """Check if raw data is identical to what was assessed in the previous run.

    Compares key fields: target_version, issue count, issue numbers.

    Args:
        raw: newly collected raw data dict
        assessment_path: path to previous assessment.json

    Returns:
        True if data is unchanged (stale), False if changed.
    """
    if assessment_path is None:
        assessment_path = config.ASSESSMENT_FILE

    if not assessment_path.exists():
        return False

    try:
        prev = load_json(assessment_path)
    except Exception:
        return False

    prev_version = prev.get("version", "")
    new_version = raw.get("target_version", "")

    # Compare version
    if prev_version != new_version:
        return False

    # Compare issue count and numbers
    new_issues = raw.get("sources", {}).get("github_issues", [])
    prev_assessment = prev.get("assessment", {})
    prev_issues = prev_assessment.get("known_issues", [])

    new_nums = sorted(i.get("number") for i in new_issues if isinstance(i, dict))
    prev_nums = sorted(i.get("number") for i in prev_issues if isinstance(i, dict))

    if new_nums != prev_nums:
        return False

    # Compare issue counts
    if len(new_issues) != len(prev_issues):
        return False

    return True


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


# ═══════════════════════════════════════════════════════════════════════════
#  Idempotent S3 Upload (placeholder for Phase 4)
# ═══════════════════════════════════════════════════════════════════════════

def smart_s3_upload(local_path: str, bucket: str, key: str, skip_if_unchanged: bool = True) -> dict:
    """Upload a file to S3, skipping if the content is unchanged.

    When fully implemented:
      1. Compute MD5 of local file
      2. HEAD the S3 object and compare ETag
      3. Skip upload if hashes match
      4. Upload with content-type auto-detection

    Args:
        local_path: path to local file
        bucket: S3 bucket name
        key: S3 object key
        skip_if_unchanged: if True, compare hashes before uploading

    Returns:
        dict with keys: uploaded (bool), skipped (bool), reason (str)
    """
    # TODO: Phase 4 — implement with boto3
    # Implementation plan:
    #   import boto3
    #   s3 = boto3.client('s3')
    #   with open(local_path, 'rb') as f:
    #       local_hash = hashlib.md5(f.read()).hexdigest()
    #   if skip_if_unchanged:
    #       try:
    #           head = s3.head_object(Bucket=bucket, Key=key)
    #           etag = head['ETag'].strip('"')
    #           if etag == local_hash:
    #               return {'uploaded': False, 'skipped': True, 'reason': 'content unchanged'}
    #       except s3.exceptions.ClientError:
    #           pass  # Object doesn't exist yet
    #   content_type = 'text/html' if local_path.endswith('.html') else 'application/json'
    #   s3.upload_file(local_path, bucket, key, ExtraArgs={'ContentType': content_type})
    #   return {'uploaded': True, 'skipped': False, 'reason': 'uploaded'}
    return {
        "uploaded": False,
        "skipped": False,
        "reason": "S3 upload not yet implemented (Phase 4 placeholder)",
    }
