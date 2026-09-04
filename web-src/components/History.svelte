<script>
  // Past verdicts, newest first; versions with an archived snapshot link to that frozen page.
  import { app } from "../lib/state.svelte.js";
  import { VERDICTS } from "../lib/tables.js";
  import { fmtDate, cap } from "../lib/fmt.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Mark from "./Mark.svelte";
  const D = $derived(app.data);
  const h = $derived.by(() => {
    const arr = (D.version_history || []).slice();
    arr.sort((a, b) => String(b.assessed_at).localeCompare(String(a.assessed_at)));
    return arr;
  });
  const arch = $derived(new Set(D.archived_versions || []));
</script>
{#if h.length}<div class="section" id="past-verdicts" use:reveal><SectionHead title="Past verdicts" count={h.length} id="past-verdicts" /><div class="timeline">{#each h as e}{@const v = VERDICTS[e.recommendation] || { tone: "tone-muted" }}{@const isCur = e.version === D.version}<div class="tl {v.tone}"><div class="marker" aria-hidden="true"><Mark rec={e.recommendation || "?"} /></div><div class="tlb"><div class="tlv">{#if !isCur && arch.has(e.version)}<a class="snap" href="archive/{encodeURIComponent(e.version)}.html" target="_blank" rel="noopener noreferrer" title="View the page as it was for v{e.version}">v{e.version}</a>{:else}v{e.version}{/if}{#if isCur}<span class="cur">current</span>{/if}</div><div class="tlr">{e.headline || e.reason || ""}</div><div class="tld">{cap(e.confidence || "") + " confidence · " + fmtDate(e.assessed_at)}</div></div></div>{/each}</div></div>{/if}
