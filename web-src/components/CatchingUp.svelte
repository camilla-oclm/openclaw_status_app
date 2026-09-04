<script>
  // Catching up: the cumulative changelog since the visitor's version.
  import { app } from "../lib/state.svelte.js";
  import { releaseHistory } from "../lib/sections.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";
  const D = $derived(app.data);
  const rh = $derived(releaseHistory(D));
  let picked = $state(null);
  // default: everything since the release before the latest
  const from = $derived(picked ?? (rh[1] && rh[1].version));
  const newer = $derived.by(() => {
    let idx = rh.length;
    for (let k = 0; k < rh.length; k++) { if (rh[k].version === from) { idx = k; break; } }
    return rh.slice(0, idx);   // every stable release newer than `from`
  });
  const totalH = $derived(newer.reduce((a, r) => a + (r.highlights || []).length, 0));
</script>
{#if rh.length >= 2}<div class="section" id="catchup" use:reveal><SectionHead title="Catching up?" note="what's changed since your version" id="catchup" /><div class="catchup"><div class="cu-row"><span class="cu-label">I'm currently on</span><select class="cu-select" aria-label="your current version" value={from} onchange={(e) => { picked = e.currentTarget.value; }}>{#each rh as r, idx}{#if idx !== 0}<option value={r.version}>v{r.version}</option>{/if}{/each}</select></div><div class="cu-out">{#if !newer.length}<div class="empty">You're on the latest stable — nothing to catch up on.</div>{:else}<div class="cu-sum">{newer.length + " release" + (newer.length > 1 ? "s" : "") + " ahead · " + totalH + " highlight" + (totalH === 1 ? "" : "s") + " since v" + from}</div>{#each newer as r}<div class="cu-rel"><div class="cu-rh"><span class="cu-ver">v{r.version}</span>{#if r.published_at}<span class="cu-date">{" · " + r.published_at}</span>{/if}</div><ul class="cu-list">{#each r.highlights || [] as h}<li><Linkify text={h} /></li>{/each}{#if !(r.highlights || []).length}<li class="empty">(no highlights parsed for this release)</li>{/if}</ul></div>{/each}{/if}</div></div></div>{/if}
