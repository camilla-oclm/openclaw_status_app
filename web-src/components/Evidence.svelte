<script>
  // The case for & against.
  import { app } from "../lib/state.svelte.js";
  import { evidenceCards } from "../lib/sections.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";
  const cards = $derived(evidenceCards(app.data));
</script>
<div class="section" id="the-case-for-against" use:reveal>
  <SectionHead title={"The case for & against"} id="the-case-for-against" />
  <div class="ev-grid">{#each cards as c}<div class="ev-card {c.cls}"><h3>{c.title}</h3><ul>{#each c.items as it}<li><Linkify text={it} /></li>{/each}</ul></div>{/each}</div>
</div>
