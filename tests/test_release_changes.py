"""Tests for openclaw_status.release_changes — deterministic changelog extraction."""

from openclaw_status import release_changes as rc

# A realistic OpenClaw release body: curated ### sections of bullets, then a long tail of
# contributor/PR lists that a flat head-truncation used to keep instead of the Fixes section.
_BODY = """## 2026.6.9

### Highlights

- **Richer Telegram delivery:** rich HTML, sticker paths, progress drafts. (#93286, #93164) Thanks @obviyus, @vincentkoc.
- **More dependable agent recovery:** retries, terminal outcomes, session history repair. (#92191) Thanks @ai-hpc.

### Changes

- Providers and auth: add Codex Hosted Search, improve Gemini OAuth. (#93446) Thanks @fuller-stack-dev.

### Fixes

- Security and privacy: redact secrets from debug output, block internal session overrides. (#93333, #88496) Thanks @Alix-007.
- Agent and session runtime: retry empty post-tool turns, prevent duplicate hook execution. (#92191) Thanks @lml2468.
- Channels and replies: fix Telegram rich delivery and table rendering. (#93286) Thanks @obviyus.

### Complete contribution record

- @somebody (1 PR)
- @another (3 PRs)
""" + "\n".join(f"- #{n} merged" for n in range(95000, 95200))   # long tail


def test_parse_maps_sections_to_buckets():
    ch = rc.parse_changelog(_BODY)
    assert len(ch["features"]) == 2     # ### Highlights
    assert len(ch["fixes"]) == 3        # ### Fixes (NOT cut off by the long tail)
    assert ch["breaking"] == []         # no ### Breaking section; ### Changes is NOT breaking


def test_changes_section_is_not_treated_as_breaking():
    # The general "### Changes" section must not inflate the breaking count.
    ch = rc.parse_changelog(_BODY)
    assert all("Providers and auth" not in (b.get("title") or "") for b in ch["breaking"])


def test_feature_title_and_value_split_from_bold_lead():
    ch = rc.parse_changelog(_BODY)
    f = ch["features"][0]
    assert f["title"] == "Richer Telegram delivery"
    assert f["value"].startswith("rich HTML")
    assert "#93286" not in f["value"] and "Thanks" not in f["value"]   # refs + attribution stripped


def test_fix_title_from_plain_category_and_verified_flag():
    ch = rc.parse_changelog(_BODY)
    titles = [f["title"] for f in ch["fixes"]]
    assert "Security and privacy" in titles
    assert all(f["verified"] is True for f in ch["fixes"])


def test_explicit_breaking_section_is_captured():
    body = "### Breaking Changes\n\n- Removed the legacy `--foo` flag; use `--bar`.\n"
    ch = rc.parse_changelog(body)
    assert len(ch["breaking"]) == 1
    assert ch["breaking"][0]["title"].startswith("Removed the legacy")


def test_changes_for_release_uses_parse_when_structured():
    out = rc.changes_for_release(_BODY, fallback={"fixes": [{"title": "llm-guess"}]})
    assert len(out["fixes"]) == 3                       # parsed wins
    assert all(f["title"] != "llm-guess" for f in out["fixes"])


def test_changes_for_release_falls_back_on_unstructured_body():
    fallback = {"breaking": [], "fixes": [{"title": "f"}], "features": [{"title": "x"}]}
    out = rc.changes_for_release("just prose, no sections at all", fallback=fallback)
    assert out == rc._norm(fallback)


def test_changes_for_release_empty_when_nothing_to_go_on():
    assert rc.is_empty(rc.changes_for_release("", fallback=None))


def test_curated_changelog_keeps_sections_drops_tail_and_round_trips():
    out = rc.curated_changelog(_BODY)
    assert "### Fixes" in out and "Channels and replies" in out      # full Fixes section kept
    assert "Complete contribution record" not in out                # bulky tail dropped
    assert "#95100 merged" not in out
    assert len(out) < len(_BODY)
    # The stored (curated) body must parse to the SAME counts as the full body — i.e. storage
    # never loses a fix (the regression: the Fixes section used to be truncated away on storage).
    assert rc.parse_changelog(out)["fixes"] == rc.parse_changelog(_BODY)["fixes"]


def test_curated_changelog_passes_through_unstructured_body():
    body = "plain prose with no sections " * 3
    assert rc.curated_changelog(body).startswith("plain prose")


def test_prompt_changelog_includes_fixes_section_and_is_bounded():
    out = rc.prompt_changelog(_BODY)
    assert "### Fixes" in out
    assert "Security and privacy" in out          # the fixes the analyst must see
    assert "#95100 merged" not in out             # the long PR tail is excluded
    assert len(out) < len(_BODY)


def test_prompt_changelog_falls_back_to_head_slice_without_sections():
    body = "x" * 9000
    assert rc.prompt_changelog(body) == body[:3000]


def test_norm_coerces_shape_and_drops_junk():
    assert rc._norm(None) == {"breaking": [], "fixes": [], "features": []}
    assert rc._norm({"fixes": "not-a-list", "features": [1], "junk": 2}) == {
        "breaking": [], "fixes": [], "features": [1]}


# ── audit L4/L5/L12: no cap, merge duplicate headings, tolerate heading variants ──

def test_no_per_bucket_cap_counts_all_fixes():
    """L4: the parser must not silently cap a bucket — the displayed 'fixes shipped' count
    has to equal what the changelog literally lists, even past a dozen."""
    body = "### Fixes\n\n" + "".join(f"- fix number {i}\n" for i in range(1, 19))   # 18 fixes
    ch = rc.parse_changelog(body)
    assert len(ch["fixes"]) == 18


def test_duplicate_same_named_sections_are_merged():
    """L5: a body that repeats '### Fixes' (split notes) must keep BOTH blocks, not just the first."""
    body = "### Fixes\n\n- alpha\n\n### Fixes\n\n- beta\n- gamma\n"
    titles = [f["title"] for f in rc.parse_changelog(body)["fixes"]]
    assert titles == ["alpha", "beta", "gamma"]


def test_heading_variants_map_to_buckets():
    """L12: a renamed heading ('### Bug Fixes', '### What's New') must still land in the right
    bucket instead of parsing to zero."""
    body = "### Bug Fixes\n\n- patched a crash\n\n### What's New\n\n- shiny thing\n"
    ch = rc.parse_changelog(body)
    assert [f["title"] for f in ch["fixes"]] == ["patched a crash"]
    assert [f["title"] for f in ch["features"]] == ["shiny thing"]


def test_md_links_unwrapped_in_parsed_items():
    """A bullet carrying raw markdown links must parse to clean text — the page renders
    plain text, so "[#82909](url)" would show literally (the live What's-new leak)."""
    body = (
        "### Fixes\n\n"
        "- Telegram reply chains keep cached replies attached."
        " [#82909](https://github.com/openclaw/openclaw/pull/82909) Thanks @lidge-jun.\n"
        "- Slack SecretRef reads use resolved credentials."
        " [7da955f](https://github.com/openclaw/openclaw/commit/7da955fae4ca2083599aa33a1f93dbfff53cb187)\n"
    )
    ch = rc.parse_changelog(body)
    assert len(ch["fixes"]) == 2                    # unwrapping never changes the count
    joined = " | ".join(f["title"] for f in ch["fixes"])
    assert "](http" not in joined and "[" not in joined
    assert "#82909" in joined                       # the ref text survives (page linkifies it)
    assert "7da955f" in joined


def test_md_link_unwrapped_before_title_split():
    """The URL's '://' colon must not trip the 'Category: description' title heuristic —
    links unwrap BEFORE the split."""
    body = "### Fixes\n\n- [Session guide](https://docs.example.com/how): updated for v2.\n"
    fix = rc.parse_changelog(body)["fixes"][0]
    assert fix["title"] == "Session guide"


def test_bold_md_link_lead_still_splits_title():
    body = "### Highlights\n\n- **[Turbo mode](https://ex.com/x):** twice the speed.\n"
    feat = rc.parse_changelog(body)["features"][0]
    assert feat["title"] == "Turbo mode"
    assert feat["value"].startswith("twice the speed")


def test_full_changelog_tail_is_not_curated():
    """The bulky '### Full Changelog' PR-log tail must stay OUT of the curated changelog even
    though its name contains 'change'."""
    body = "### Highlights\n\n- a feature\n\n### Full Changelog\n\n- #1 by @x\n- #2 by @y\n"
    curated = rc.curated_changelog(body)
    assert "a feature" in curated
    assert "Full Changelog" not in curated
