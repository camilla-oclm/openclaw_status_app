<script>
  // The analyst's full reasoning, with community sentiment folded in (it's the analyst's read
  // of the same evidence, so it belongs with the reasoning rather than as a standalone block).
  import { app } from "../lib/state.svelte.js";
  import { reveal, stagger } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";
  const D = $derived(app.data);
  const paras = $derived(String(D.thesis).split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean));
</script>
<div class="section" id="why-this-verdict" use:reveal>
  <SectionHead title="Why this verdict" id="why-this-verdict" />
  <div class="prose" use:stagger>{#each paras as p}<p><Linkify text={p} /></p>{/each}</div>
  {#if D.sentiment_summary}<div class="quote"><div class="qh">Community sentiment</div><div><Linkify text={D.sentiment_summary} /></div><div class="qsrc">Read by the analyst from GitHub issue signals — reaction counts, comment volume and how fast regressions pile up. Not scraped from social media.</div></div>{/if}
</div>
