<script>
  // What the second model actually said — expands from the review chip. Shows the exchange's
  // outcome, the validator's own words, and its specific findings when the pipeline persisted
  // them (review.detail; runs before the field carry only the short critique).
  import { app } from "../lib/state.svelte.js";
  import Linkify from "./Linkify.svelte";
  let { rv, hidden } = $props();
  const D = $derived(app.data);
  const d = $derived(rv.detail || {});
  const status = $derived.by(() => {
    if (rv.refined && rv.primary_recommendation && rv.primary_recommendation !== (D.recommendation || ""))
      return "An independent second model challenged the first analyst's read (" + rv.primary_recommendation + ") and the published verdict was revised to " + (D.recommendation || "") + " after a refinement pass.";
    if (rv.refined) return "An independent second model flagged the specifics below; the analyst re-checked each one and the verdict held.";
    if (rv.agreed) return "An independent second model re-derived the top issues' severity, category and platforms from the raw data and concurred with this verdict.";
    return "An independent second model disagreed with details of this verdict; the analyst's re-check pass failed to complete this run, so shown is the analyst's original read.";
  });
  const crit = $derived(String(d.critique || rv.critique || "").trim());
  const parts = $derived([
    ["Mis-categorizations it flagged", d.miscategorized_issues],
    ["Issues it flagged as missed", d.missed_issues],
    ["Reasoning it challenged", (d.logical_errors || []).concat(d.overruled_claims || [])],
  ].filter((p) => p[1] && p[1].length));
</script>
<div class="rev-detail" id="rev-detail" role="region" aria-label="Second-model review detail" {hidden}>
  <div class="rvd-h">Second-model review</div>
  <p class="rvd-status">{status}</p>
  {#if crit}<blockquote class="rvd-quote">“{crit}”</blockquote>{/if}
  {#each parts as p}<div class="rvd-k">{p[0]}</div><ul class="rvd-list">{#each p[1] as it}<li><Linkify text={String(it)} /></li>{/each}</ul>{/each}
  {#if d.suggested_recommendation && d.suggested_recommendation !== (D.recommendation || "")}<p class="rvd-note">The validator's own suggestion was {d.suggested_recommendation} — the published verdict is the analyst's final call after this exchange.</p>{/if}
  <p class="rvd-note">The analyst and validator run on different model providers, so a verdict is never a model approving its own reasoning.</p>
</div>
