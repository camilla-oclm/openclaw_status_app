<script>
  // What would change this verdict — the analyst's concrete, checkable tripwires, so the
  // verdict is falsifiable rather than a black box.
  import { app } from "../lib/state.svelte.js";
  import { flipList } from "../lib/sections.js";
  import { reveal, stagger } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";
  const fc = $derived(flipList(app.data));
</script>
<div class="section" id="flip" use:reveal>
  <SectionHead title="What would change this verdict" note="tripwires we watch between runs" id="flip" />
  <div class="flip-card"><ul use:stagger>{#each fc as c}<li><Linkify text={String(c)} /></li>{/each}</ul><div class="flip-note">Stated by the analyst with the verdict, re-checked every run — if one of these happens, the next assessment should move (see the track record under History).</div></div>
</div>
