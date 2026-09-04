<script>
  // The long-tail tabs inside the evidence: Impact · What's new · Trends · History. Empty groups
  // drop their tab entirely. The switch runs through the View Transitions API where it exists
  // (a root crossfade); the initial tab and deep links stay synchronous.
  import { flushSync } from "svelte";
  import { app } from "../lib/state.svelte.js";
  import { withTransition, registry, revealAll } from "../lib/dom.js";
  import { hasTriage, changeGroups, hasCatchup, hasTrends, hasTrackRecord, hasHistory, componentAgg, platformAgg } from "../lib/sections.js";
  import Impact from "./Impact.svelte";
  import Triage from "./Triage.svelte";
  import Changes from "./Changes.svelte";
  import CatchingUp from "./CatchingUp.svelte";
  import Trends from "./Trends.svelte";
  import TrackRecord from "./TrackRecord.svelte";
  import History from "./History.svelte";

  const D = $derived(app.data);
  const groups = $derived.by(() => {
    const hasImpact = !!(componentAgg(D, app.stack) || platformAgg(D, app.stack));
    return [
      { key: "impact", label: "Impact", has: hasImpact || hasTriage(D) },
      { key: "new", label: "What's new", has: changeGroups(D).length > 0 || hasCatchup(D) },
      { key: "trends", label: "Trends", has: hasTrends(D) },
      { key: "history", label: "History", has: hasTrackRecord(D) || hasHistory(D) },
    ].filter((g) => g.has);
  });
  let active = $state(0);
  let panels = $state([]);
  $effect(() => { if (active >= groups.length) active = 0; });
  function activate(i, instant) {
    withTransition(() => {
      active = i;
      flushSync();
      // sections in a freshly-shown panel never tripped the IntersectionObserver — reveal directly
      revealAll(panels[i]);
    }, instant);
  }
  // Deep links: open the tab that holds the target, instantly (scrollToHash scrolls right after).
  registry.activateLongTab = (panel) => { const k = panels.indexOf(panel); if (k >= 0) activate(k, true); };
  $effect(() => () => { registry.activateLongTab = null; });
  function keydown(ev) {
    const tabs = Array.from(ev.currentTarget.querySelectorAll(".ltab"));
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let j = -1;
    if (ev.key === "ArrowRight" || ev.key === "ArrowDown") j = (i + 1) % tabs.length;
    else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") j = (i - 1 + tabs.length) % tabs.length;
    else if (ev.key === "Home") j = 0;
    else if (ev.key === "End") j = tabs.length - 1;
    if (j < 0) return;
    ev.preventDefault(); activate(j); tabs[j].focus();
  }
</script>
<!-- svelte-ignore a11y_interactive_supports_focus -->
{#if groups.length}<div class="ltab-wrap"><div class="ltabs" role="tablist" aria-label="More detail" onkeydown={keydown}>{#each groups as g, i (g.key)}<button class="ltab" id="ltt-{g.key}" type="button" role="tab" aria-controls="ltp-{g.key}" aria-selected={i === active ? "true" : "false"} tabindex={i === active ? "0" : "-1"} onclick={() => activate(i)}>{g.label}</button>{/each}</div><div class="ltab-panels">{#each groups as g, i (g.key)}<div class="ltab-panel" id="ltp-{g.key}" role="tabpanel" aria-labelledby="ltt-{g.key}" tabindex="0" hidden={i !== active} bind:this={panels[i]}>{#if g.key === "impact"}<Impact /><Triage />{:else if g.key === "new"}<Changes /><CatchingUp />{:else if g.key === "trends"}<Trends />{:else}<TrackRecord /><History />{/if}</div>{/each}</div></div>{/if}
