<script>
  // Trends: 2×2 time-series charts from the per-run timeline (hidden until ≥3 real points).
  import { app } from "../lib/state.svelte.js";
  import { prepTimeline, issuesCap, pressureSpec, severitySpec, verdictSpec, shareSpec } from "../lib/charts.js";
  import { reveal, stagger } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Chart from "./Chart.svelte";
  const D = $derived(app.data);
  const tl = $derived(prepTimeline(D));
  const cap = $derived(issuesCap(D));
  const cards = $derived(tl ? [
    { title: "Issue pressure", sub: "open issues & regressions per run", spec: pressureSpec(tl, cap) },
    { title: "Severity mix", sub: "make-up of known issues", spec: severitySpec(tl, cap) },
    { title: "Verdict", sub: "risk per run · higher = riskier", spec: verdictSpec(tl) },
    { title: "Regression share", sub: "confirmed regressions as a share of known issues", spec: shareSpec(tl) },
  ] : []);
</script>
{#if tl}<div class="section" id="trends" use:reveal><SectionHead title="Trends" note="tracked per assessment · adaptive cadence (8–48h)" id="trends" /><div class="trends-grid" use:stagger>{#each cards as card}<div class="tcard"><div class="tc-h"><span class="tc-t">{card.title}</span><span class="tc-s">{card.sub}</span></div>{#if card.spec}<Chart spec={card.spec} title={card.title} sub={card.sub} />{:else}<div class="tc-empty">Collecting — fills in as runs accumulate</div>{/if}</div>{/each}</div></div>{/if}
