<script>
  // Best version to run today (server-computed: verdict.recommended_version).
  import { app } from "../lib/state.svelte.js";
  import { VERDICTS, VERDICT_TONE } from "../lib/tables.js";
  import { recoLine } from "../lib/verdict.js";
  import { fmtDate, issueUrl } from "../lib/fmt.js";
  import { announce, reveal } from "../lib/dom.js";
  import Mark from "./Mark.svelte";

  const D = $derived(app.data);
  const rv = $derived(D.recommended_version);
  const tone = $derived(!rv.version ? "tone-muted" : (rv.recommendation === "✅" ? "tone-good" : "tone-info"));
  const v = $derived(VERDICTS[rv.recommendation] || { label: String(rv.recommendation || "") });
  const n = $derived(rv.blocker_count || 0);
  const cmd = $derived("npm install -g openclaw@" + rv.version);
  const archived = $derived((D.archived_versions || []).indexOf(rv.version) >= 0);
  let copyLabel = $state("⧉ copy");
  function trunc(t) { t = String(t || ""); return t.length > 96 ? t.slice(0, 93).replace(/\s+\S*$/, "") + "…" : t; }
  function copy() {
    const ok = () => { announce("Install command copied."); copyLabel = "✓ copied"; setTimeout(() => { copyLabel = "⧉ copy"; }, 1400); };
    const ko = () => announce("Couldn't copy automatically — select the command text instead.");
    try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(cmd).then(ok, ko); else ko(); } catch (e) { ko(); }
  }
</script>
<div class="reco {tone}" id="best-version" use:reveal>
  <div class="reco-k">Best version to run today</div>
  <div class="reco-v">{rv.version ? "v" + rv.version : "No release has settled yet"}</div>
  <p class="reco-why">{recoLine(rv)}</p>
  {#if rv.version}<div class="reco-chips">{#if rv.published_at}<span class="chip">Released <b>{fmtDate(rv.published_at)}</b></span>{/if}{#if rv.recommendation}<span class="chip {VERDICT_TONE[rv.recommendation] || ''}" title="The last verdict this tool gave that release">Last verdict: <b><Mark rec={rv.recommendation} size={13} />{" " + v.label.toLowerCase()}</b></span>{/if}<span class="chip" title="Open high/critical issues confirmed for that version with a person or the community behind them — none drew megathread-class engagement">Known issues: <b>{n ? n + " credible, none widespread" : "none credible"}</b></span></div>{/if}
  {#if rv.version && rv.blockers && rv.blockers.length}<div class="reco-kk">Known problems to read first</div><ul class="reco-issues">{#each rv.blockers.slice(0, 4) as b}<li><a class="ilink" href={issueUrl(b.number)} target="_blank" rel="noopener noreferrer">#{b.number}</a>{" " + trunc(b.title)}<span class="why-meta">{b.severity ? " · " + b.severity : ""}</span></li>{/each}</ul>{/if}
  {#if rv.version}<div class="reco-cmdrow"><span class="cmd-k">Pin it:</span><code class="reco-cmd">{cmd}</code><button class="pick pick-copy" type="button" title="Copy the pinned install command" onclick={copy}>{copyLabel}</button></div>{#if rv.version !== (D.version || "") && archived}<a class="reco-link" href="archive/{encodeURIComponent(rv.version)}.html" target="_blank" rel="noopener noreferrer">Read that release's own verdict page ↗</a>{/if}{/if}
  <div class="reco-note">Deterministic rule, no model: the newest release we've assessed that has been out at least {rv.min_days || 7} days, wasn't rated skip, and shows no widespread breaker in its own issue ledger.</div>
</div>
