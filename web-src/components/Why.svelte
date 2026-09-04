<script>
  // Why: what's broken (the gate's credible blockers) vs what you'd get (the release notes).
  import { app } from "../lib/state.svelte.js";
  import { PLAT_LABEL } from "../lib/tables.js";
  import { gateBlockers, issuePlatforms } from "../lib/verdict.js";
  import { issueUrl } from "../lib/fmt.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";

  const D = $derived(app.data);
  const bl = $derived(gateBlockers());
  const ch = $derived(D.changes || {});
  const ki = $derived(D.known_issues || []);
  const eg = $derived(D.evidence_gate || {});
  const subBits = $derived.by(() => {
    const b = [];
    if (eg.reason) b.push(eg.reason);
    if (eg.departure && eg.departure.departed) b.push("The analyst chose a more cautious call" + (eg.departure.reason ? ": " + eg.departure.reason : "."));
    return b;
  });
  const fixes = $derived((ch.fixes || []).length);
  const feats = $derived((ch.features || []).length);
  const brk = $derived((ch.breaking || []).length);
  const counts = $derived.by(() => {
    const c = [];
    if (feats) c.push(feats + " highlight" + (feats === 1 ? "" : "s"));
    if (fixes) c.push(fixes + " fix" + (fixes === 1 ? "" : "es") + ((D.hotfix_chain || []).length > 1 ? " across " + D.hotfix_chain.length + " stacked hotfixes" : ""));
    c.push(brk ? brk + " breaking change" + (brk === 1 ? "" : "s") : "no breaking changes listed");
    return c;
  });
  const rest = $derived(bl.length - Math.min(bl.length, 4));
  function plat(i) {
    const ps = issuePlatforms(i);
    return ps.indexOf("all") >= 0 ? "all platforms" : ps.map((k) => (PLAT_LABEL[k] || [0, k])[1]).join(", ");
  }
</script>
<div class="section" id="why" use:reveal>
  <SectionHead title="Why" note="the evidence behind the answer" id="why" />
  <div class="why-grid">
    <div class="why-card {bl.length ? 'why-bad' : 'why-ok'}">
      <h3>{bl.length ? "What's broken · " + bl.length + " credible blocking issue" + (bl.length === 1 ? "" : "s") : "Nothing credible is blocking"}</h3>
      {#if subBits.length}<p class="why-sub">{subBits.join(" ")}</p>{/if}
      {#if bl.length}<ul>{#each bl.slice(0, 4) as i}{@const pl = plat(i)}<li><a class="ilink" href={issueUrl(i.number)} target="_blank" rel="noopener noreferrer">#{i.number}</a>{" " + (i.title || "")}<span class="why-meta">{" — " + (i.severity || "") + (pl ? " · " + pl : "")}</span></li>{/each}</ul><p class="why-more">{(rest ? "+" + rest + " more, " : "") + "all " + ki.length + (D.issues_capped ? "+" : "") + " known issues are in the full evidence below."}</p>{:else if !eg.reason}<p class="why-sub">{ki.length ? "The " + ki.length + (D.issues_capped ? "+" : "") + " tracked reports for this release carry bot-applied labels with no human corroboration or community traction — normal ambient churn for a project with millions of weekly installs." : "No issues are tracked for this release yet."}</p>{/if}
    </div>
    <div class="why-card why-good">
      <h3>What you'd get</h3>
      <p class="why-sub">{counts.join(" · ")}</p>
      {#if feats}<ul>{#each (ch.features || []).slice(0, 4) as f}<li><Linkify text={typeof f === "string" ? f : (f.title || "")} /></li>{/each}</ul>{/if}
      {#if fixes || feats || brk}<p class="why-more">The full changelog is in the evidence below, under What's new.</p>{/if}
    </div>
  </div>
</div>
