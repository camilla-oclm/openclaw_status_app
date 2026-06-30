"""
Deterministic changelog extraction — parse a release's `changes` straight from its body.

A released version's changelog (the release body) is immutable and *structured*: OpenClaw
release notes carry `### Highlights`, `### Changes`, and `### Fixes` sections of `- ` bullets.
The analyst LLM used to re-parse that text every run, which (a) drifted run-to-run with model
variance and (b) silently dropped whole sections when the body was truncated before the LLM
ever saw them — e.g. the `### Fixes` section sits far past the first few KB on a big release,
so "fixes shipped" rendered as 0 even though the changelog listed seven.

So we don't ask the model for this at all. `parse_changelog` reads the sections directly:
features ← Highlights, fixes ← Fixes, breaking ← an explicit "Breaking" section (general
"### Changes" are NOT breaking and are intentionally left out of the counts). The result is
exact, stable, and free — the displayed counts equal what the changelog literally says. The
LLM's own extraction is kept only as a fallback for an unstructured/edited body that yields
no recognizable sections (`changes_for_release`).
"""

import re

_KEYS = ("breaking", "fixes", "features")

# Section headers (## … ####) and top-level bullets within a section.
_HEADER_RE = re.compile(r"^#{2,4}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^[-*][ \t]+(.+?)[ \t]*$", re.MULTILINE)
# A bold lead-in ("**Title:** …") or a "Category: …" plain bullet.
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*:?[ \t]*")
# Trailing PR/issue refs "(#123, #456)" and the "Thanks @a, @b." attribution tail.
_REFS_RE = re.compile(r"\s*\((?:\s*#\d+\s*,?)+\)")
_THANKS_RE = re.compile(r"\s*Thanks\b.*$", re.IGNORECASE)

# Map the changelog's curated sections onto the three change buckets. "### Changes" is a
# general catch-all (providers, plugins, dashboard, QA) — real improvements, but NOT breaking
# changes and largely a restatement of Highlights, so it is deliberately excluded from the counts.


def _norm(changes) -> dict:
    """Coerce to the canonical {breaking, fixes, features: [list]} shape (drop junk/extra keys)."""
    changes = changes if isinstance(changes, dict) else {}
    return {k: (changes[k] if isinstance(changes.get(k), list) else []) for k in _KEYS}


def is_empty(changes) -> bool:
    """True when a changes dict carries no items in any bucket."""
    n = _norm(changes)
    return not any(n[k] for k in _KEYS)


def _sections(body: str) -> dict:
    """Split a markdown body into {lowercased section name: section text}. Same-named sections
    are CONCATENATED, not first-wins — a body that repeats '### Fixes' (split notes) must not
    silently drop the later block from the parsed counts or the curated changelog."""
    matches = list(_HEADER_RE.finditer(body or ""))
    out: dict = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[name] = out.get(name, "") + body[start:end]
    return out


def _clean(s: str) -> str:
    """Strip the trailing PR refs + Thanks tail and collapse whitespace."""
    s = _REFS_RE.sub("", s or "")
    s = _THANKS_RE.sub("", s)
    return " ".join(s.split()).strip()


def _split_title(bullet: str) -> tuple[str, str]:
    """Split a bullet into (title, remainder): the **bold** lead-in or the pre-colon category."""
    b = bullet.strip()
    m = _BOLD_RE.match(b)
    if m:
        return m.group(1).strip().rstrip(":").strip(), b[m.end():]
    if ":" in b[:90]:                       # "Category: description" — colon early in the line
        head, rest = b.split(":", 1)
        return head.strip(), rest
    return b, ""


def _bullets(text: str, extra_key: str | None, extra_val=None) -> list[dict]:
    """Each `- ` bullet in `text` → {title, <extra_key>: …}; skips blank titles.

    No per-bucket cap: the displayed fix/feature counts must equal what the changelog literally
    lists (a 12-item cap silently undercounted a large release), and the input is already bounded
    — parse_changelog runs on the curated, size-capped body, not the tens-of-KB raw one.
    """
    items = []
    for raw in _BULLET_RE.findall(text or ""):
        title, rest = _split_title(raw)
        title = _clean(title)
        if not title:
            continue
        item = {"title": title}
        if extra_key == "verified":
            item["verified"] = True
        elif extra_key:                      # "value" (features) / "impact" (breaking)
            item[extra_key] = _clean(rest)
        items.append(item)
    return items


def _match(secs: dict, *needles: str) -> str:
    """All sections whose lowercased name contains any needle, concatenated — so heading variants
    ('Fixes' / 'Bug Fixes', 'Highlights' / 'What's New' / 'Features') all land in the right
    bucket instead of a renamed section silently parsing to zero."""
    return "\n".join(v for k, v in secs.items() if any(n in k for n in needles))


def parse_changelog(body: str) -> dict:
    """Extract {breaking, fixes, features} straight from a release body's sections."""
    secs = _sections(body or "")
    return {
        "breaking": _bullets(_match(secs, "breaking"), "impact"),
        "fixes": _bullets(_match(secs, "fix"), "verified"),
        "features": _bullets(_match(secs, "highlight", "feature", "what's new"), "value"),
    }


def changes_for_release(body: str, fallback=None) -> dict:
    """The deterministic `changes` for a release body, or the LLM `fallback` if the body has
    no recognizable sections (unstructured / edited changelog)."""
    parsed = parse_changelog(body)
    return parsed if not is_empty(parsed) else _norm(fallback)


# The curated sections — the part of a release body worth keeping (the rest is the long
# contributor list / full PR log). Highlights/Changes/Fixes/Features/Breaking and their variants.
def _is_curated_section(name: str) -> bool:
    n = name.strip()
    if "changelog" in n:        # the "Full Changelog" PR-log tail — explicitly NOT curated
        return False
    return ("breaking" in n or n == "changes"
            or any(k in n for k in ("highlight", "fix", "feature", "what's new")))


def _curated_chunks(body: str, per_section: int | None) -> list[str]:
    """The curated sections as "### Name\\n<text>" chunks, each optionally capped to per_section."""
    chunks = []
    for name, text in _sections(body or "").items():
        if _is_curated_section(name):
            text = text.strip()
            chunks.append(f"### {name.title()}\n{text[:per_section] if per_section else text}")
    return chunks


def curated_changelog(body: str, cap: int = 20000) -> str:
    """A release body trimmed to its curated sections — what's worth storing.

    The raw body is tens of KB (contributor lists, the full PR log) but the meaningful content
    is the Highlights/Changes/Fixes/Breaking sections (~a few KB). Storing only those keeps the
    *whole* ### Fixes section so the fix/feature counts parsed downstream are complete (a flat
    head-truncation used to slice ### Fixes off, rendering fixes as 0), while dropping the bulky
    tail. Full sections (not per-section capped); total bounded by `cap`. Returns the body
    unchanged (capped) if it has no recognizable sections.
    """
    chunks = _curated_chunks(body, per_section=None)
    return ("\n\n".join(chunks) if chunks else (body or "")).strip()[:cap]


def prompt_changelog(body: str, per_section: int = 2500, fallback_chars: int = 3000) -> str:
    """The curated sections for the LLM prompt, each capped (token control). Head-slice fallback."""
    chunks = _curated_chunks(body, per_section=per_section)
    return "\n\n".join(chunks) if chunks else (body or "")[:fallback_chars]
