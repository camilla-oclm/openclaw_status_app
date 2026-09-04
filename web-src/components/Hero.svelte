<script>
  // The answer: eyebrow, version, the status word with its mark, one plain sentence, the
  // analyst's headline, and the meta line (confidence · fix staged · second-model review ·
  // evidence gate) — with the setup card in the second column.
  import { flushSync } from "svelte";
  import { app } from "../lib/state.svelte.js";
  import { VERDICTS } from "../lib/tables.js";
  import { statusOf, platformVerdicts, answerLine, confNote, verdictWord } from "../lib/verdict.js";
  import { fmtDate, timeAgo, whenText, cap, safeUrl } from "../lib/fmt.js";
  import Mark from "./Mark.svelte";
  import Icon from "./Icon.svelte";
  import ReviewDetail from "./ReviewDetail.svelte";
  import Setup from "./Setup.svelte";

  const D = $derived(app.data);
  const st = $derived(statusOf());
  const pvs = $derived(platformVerdicts());
  const rel = $derived(D.latest_release || {});
  const relUrl = $derived(safeUrl(rel.url));
  const sub = $derived.by(() => {
    const s = [], npm = D.npm || {};
    if (rel.published_at) {
      const d = (D.freshness || {}).days_since_release;
      s.push("Released " + fmtDate(rel.published_at) + (d != null ? " (" + whenText(d) + ")" : ""));
    }
    if (D.assessed_at) s.push("assessed " + timeAgo(D.assessed_at));
    if (npm.version) s.push("npm " + npm.version);
    return s;
  });
  const conf = $derived(String(D.confidence || "medium").toLowerCase());
  const pre = $derived(D.latest_prerelease || {});
  const preUrl = $derived(safeUrl(pre.url));
  const preTip = "A newer release is staged in pre-release. Until those fixes ship in a stable release, the current version still carries the issues below.";
  // Dual-model review outcome — the central reason to trust an automated verdict: a second,
  // independent model concurred, or the verdict was REVISED after it pushed back.
  const rv = $derived(D.review || {});
  const revised = $derived(!!(rv.refined && rv.primary_recommendation && rv.primary_recommendation !== (D.recommendation || "")));
  const rvText = $derived(rv.agreed ? "Second model agreed" : (rv.refined ? "Second model flagged, verdict held" : "Second model flagged, re-check failed"));
  const rvTip = $derived(revised
    ? "The first analyst model said " + rv.primary_recommendation + "; an independent second model challenged it and the verdict was revised."
    : rv.agreed ? "An independent second model reviewed this verdict and concurred."
    : rv.refined ? "An independent second model flagged details, but the verdict held after re-check."
    : "An independent second model flagged details, but the analyst's re-check pass failed this run — shown is the analyst's original read.");
  const eg = $derived(D.evidence_gate || {});
  const egTip = "Deterministic floor computed from the issue evidence alone (open high/critical issues confirmed for this version with a person or the community behind them). The analyst may only be MORE cautious than it, with a cited reason.";
  const glyph = $derived(st.key === "wait" ? "⏳" : (D.recommendation || "?"));
  const toneOf = (r) => (VERDICTS[r] || {}).tone || "tone-muted";
  let rvOpen = $state(false);
</script>
<div class="hero {st.tone}">
  <div class="hero-main">
    <div class="eyebrow">OpenClaw · latest release</div>
    <h1 class="hero-version">OpenClaw <span class="vnum">v{D.version || "?"}</span></h1>
    <div class="hero-sub">{#each sub as x, i}{#if i}<span class="sep">·</span>{/if}{x}{/each}</div>
    {#if relUrl}<a class="verdict {st.tone}" href={relUrl} target="_blank" rel="noopener noreferrer" title="View the OpenClaw v{D.version || ''} release on GitHub"><span class="em" aria-hidden="true"><Mark rec={glyph} /></span><span class="answer-word">{st.label}</span><span class="v-ext" aria-hidden="true">↗</span></a>{:else}<div class="verdict {st.tone}"><span class="em" aria-hidden="true"><Mark rec={glyph} /></span><span class="answer-word">{st.label}</span></div>{/if}
    <p class="answer-line">{answerLine(st, pvs)}</p>
    {#if String(D.confidence || "").toLowerCase() === "low"}<div class="banner"><span class="bi" aria-hidden="true"><Mark rec="⚠️" /></span><span>This assessment was made with low confidence — the underlying data may be incomplete. Treat the verdict as a weak signal and verify against the linked issues before deciding.</span></div>{/if}
    {#if D.headline}<p class="hero-headline"><span class="hh-k">Analyst</span>{D.headline}</p>{/if}
    <div class="conf-row"><span class="chip lvl-{conf}" title={confNote(conf)}>Confidence: <b>{cap(conf)}</b></span>{#if pre.tag}{#if preUrl}<a class="chip chip-link" href={preUrl} target="_blank" rel="noopener noreferrer" title="View {pre.tag} on GitHub. {preTip}"><b>Fix staged: {pre.tag}</b> (pre-release)<span class="v-ext" aria-hidden="true">↗</span></a>{:else}<span class="chip" title={preTip}><b>Fix staged: {pre.tag}</b> (pre-release)</span>{/if}{/if}{#if rv.validated}<button class="chip" type="button" title="{rvTip} Click for what it said." aria-expanded={rvOpen ? "true" : "false"} aria-controls="rev-detail" onclick={() => { rvOpen = !rvOpen; flushSync(); }}><Icon k="review" size={13} /><b>{#if revised}Revised <span class="vgw {toneOf(rv.primary_recommendation)}"><Mark rec={rv.primary_recommendation} size={13} /></span> → <span class="vgw {toneOf(D.recommendation)}"><Mark rec={D.recommendation} size={13} /></span> on review{:else}{rvText}{/if}</b><span class="cx" aria-hidden="true">▾</span></button>{:else if rv.unreviewed}<span class="chip" title="The independent validator model was unavailable on this run, so this verdict reflects a single model — confidence is capped and you should sanity-check it against the linked issues."><Icon k="review" size={13} /><b>Single model — validator unavailable</b></span>{/if}{#if eg.verdict}<span class="chip" title="{egTip} {eg.reason || ''}">Evidence gate: <b class={toneOf(eg.verdict)}><Mark rec={eg.verdict} size={13} /> {verdictWord(eg.verdict)}</b></span>{/if}</div>
    {#if rv.validated}<ReviewDetail {rv} hidden={!rvOpen} />{/if}
  </div>
  <Setup {pvs} />
</div>
