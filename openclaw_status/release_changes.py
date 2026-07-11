"""
Deterministic changelog extraction — parse a release's `changes` straight from its body.

A released version's changelog (the release body) is immutable and *structured*: OpenClaw
release notes carry `### Highlights`, `### Changes`, and `### Fixes` sections of `- ` bullets.
The analyst LLM used to re-parse that text every run, which (a) drifted run-to-run with model
variance and (b) silently dropped whole sections when the body was truncated before the LLM
ever saw them — e.g. the `### Fixes` section sits far past the first few KB on a big release,
so "fixes shipped" rendered as 0 even though the changelog listed seven.

So we don't ask the model for this at all. `parse_changelog` reads the sections directly:
features ← the Highlights subtree, fixes ← Fixes, breaking ← an explicit "Breaking" section
(general "### Changes" are NOT breaking and are intentionally left out of the counts). The
result is exact, stable, and free — the displayed counts equal what the changelog literally
says. The LLM's own extraction is kept only as a fallback for an unstructured/edited body
that yields no recognizable sections (`changes_for_release`).

Sectioning is HIERARCHICAL (added 2026-07-11): OpenClaw's notes evolved from flat
`### Highlights / ### Changes / ### Fixes` bullet lists to themed `####` subsections —
`### Highlights` now holds `#### <theme>` blocks ("Slack router relay mode", …) and each
`### <Area>` section ends with an `#### Additional <area> fixes` tail. Flat name-matching
saw the Highlights bullets as unrecognized subsections and parsed features to ZERO (and on
bodies with no "fix"-named subsection at all, everything to zero → silent LLM fallback).
A section now buckets by its OWN name first (most specific — an "… fixes" subsection under
Highlights stays a fix), then its PARENT's, so the whole Highlights subtree lands in
features. Themed subsections under a non-matching area parent stay out of the counts — the
new-format analog of the old excluded "### Changes" catch-all.
"""

import re

from openclaw_status.lib import strip_md_links

_KEYS = ("breaking", "fixes", "features")

# Section headers (## … ####, level captured for hierarchy) and top-level bullets.
_HEADER_RE = re.compile(r"^(#{2,4})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
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


def _sections(body: str) -> list:
    """Split a markdown body into [(own_name, parent_name, level, text)] in document order —
    each section's text runs to the next header of ANY level, and its parent is the nearest
    open shallower header (a `####` under `### Highlights` knows it's a highlight). Duplicate
    same-named sections each get their own entry, preserved in order — a body that repeats
    '### Fixes' (split notes) must not silently drop the later block from the parsed counts
    or the curated changelog."""
    matches = list(_HEADER_RE.finditer(body or ""))
    out = []
    open_at = {}                               # level → name of the currently-open header
    for i, m in enumerate(matches):
        level = len(m.group(1))
        name = m.group(2).strip().lower()
        parent = ""
        for lvl in range(level - 1, 1, -1):    # nearest open ancestor (## is the shallowest)
            if lvl in open_at:
                parent = open_at[lvl]
                break
        open_at = {lvl: n for lvl, n in open_at.items() if lvl < level}
        open_at[level] = name
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((name, parent, level, body[start:end]))
    return out


_FEATURE_NEEDLES = ("highlight", "feature", "what's new")


def _bucket(own: str, parent: str) -> str | None:
    """Which bucket a section's bullets count toward — by its OWN name first (most specific:
    an '… fixes' subsection stays a fix even inside Highlights), then its parent's, so every
    themed subsection nested under Highlights lands in features. None = not counted (the
    general '### Changes' catch-all, themed area subsections, contributor/QA tails)."""
    for name in (own, parent):
        if "breaking" in name:
            return "breaking"
        if "fix" in name:
            return "fixes"
        if any(n in name for n in _FEATURE_NEEDLES):
            return "features"
    return None


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
        # Unwrap "[#N](url)" links BEFORE the title split — the URL's "://" colon would
        # otherwise trip the "Category: description" heuristic, and downstream the page
        # renders plain text, so a wrapped link would show its raw markdown literally.
        raw = strip_md_links(raw)
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


_EXTRA_KEY = {"breaking": "impact", "fixes": "verified", "features": "value"}


def parse_changelog(body: str) -> dict:
    """Extract {breaking, fixes, features} straight from a release body's sections.
    Heading variants ('Fixes' / 'Bug Fixes', 'Highlights' / 'What's New' / 'Features') land
    in the right bucket via substring matching, and nested subsections inherit their parent's
    bucket — see _bucket."""
    out = {"breaking": [], "fixes": [], "features": []}
    for own, parent, _level, text in _sections(body or ""):
        bucket = _bucket(own, parent)
        if bucket:
            out[bucket].extend(_bullets(text, _EXTRA_KEY[bucket]))
    return out


def changes_for_release(body: str, fallback=None, parsed=None) -> dict:
    """The deterministic `changes` for a release, or the LLM `fallback` if nothing parses
    (unstructured / edited changelog).

    `parsed` is the collect-time parse of the RAW body (github._norm_release stores it as
    release["changes"]) and takes precedence: the stored `body` is the curated, size-capped
    text, and a big release (v2026.6.11: 68KB of curated sections) overflows that cap — so
    counts derived from the stored body silently lose whole sections. Re-parsing `body` is
    the fallback for raw-data.json written before the collect-time parse existed."""
    if parsed is not None and not is_empty(parsed):
        return _norm(parsed)
    reparsed = parse_changelog(body)
    return reparsed if not is_empty(reparsed) else _norm(fallback)


# The curated sections — the part of a release body worth keeping (the rest is the long
# contributor list / full PR log): everything that buckets (incl. the Highlights subtree)
# plus the literal "### Changes" catch-all.
def _is_curated_section(own: str, parent: str) -> bool:
    n = own.strip()
    if "changelog" in n:        # the "Full Changelog" PR-log tail — explicitly NOT curated
        return False
    return _bucket(own, parent) is not None or n == "changes"


def _curated_chunks(body: str, per_section: int | None) -> list[str]:
    """The curated sections as header+text chunks, each optionally capped to per_section.

    Headers keep their ORIGINAL level (### vs ####) — the curated output is what gets STORED
    as the release body and later re-parsed by parse_changelog, so the Highlights→subsection
    hierarchy must survive the round-trip. A kept subsection whose (non-curated) area parent
    was dropped can only be one that buckets on its OWN name (see _is_curated_section), so
    re-parsing under a wrong inherited parent cannot change its bucket."""
    chunks = []
    for own, parent, level, text in _sections(body or ""):
        if _is_curated_section(own, parent):
            text = text.strip()
            chunks.append(f"{'#' * level} {own.title()}\n"
                          f"{text[:per_section] if per_section else text}")
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


def prompt_changelog(body: str, per_section: int = 2500, fallback_chars: int = 3000,
                     cap: int = 8000) -> str:
    """The curated sections for the LLM prompt, each capped (token control), with a TOTAL
    cap on top: the hierarchical curation brought the whole Highlights subtree back in, and
    a big release (v2026.6.11: 20+ sections) would otherwise balloon the analyst context
    that the tiering round deliberately slimmed. Document order puts Highlights first, so
    the cap sheds the least-important tail. Head-slice fallback for unstructured bodies."""
    chunks = _curated_chunks(body, per_section=per_section)
    return ("\n\n".join(chunks) if chunks else (body or "")[:fallback_chars])[:cap]
