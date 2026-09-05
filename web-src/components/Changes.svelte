<script>
  // What's new in this release — tabs per changelog group, roving tabindex + Arrow/Home/End.
  import { flushSync } from "svelte";
  import { app } from "../lib/state.svelte.js";
  import { changeGroups } from "../lib/sections.js";
  import { reveal, stagger } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Linkify from "./Linkify.svelte";
  const D = $derived(app.data);
  const groups = $derived(changeGroups(D));
  const total = $derived(groups.reduce((a, g) => a + g.items.length, 0));
  let active = $state(0);
  $effect(() => { if (active >= groups.length) active = 0; });
  function select(i) { active = i; flushSync(); }
  function keydown(ev) {
    const tabs = Array.from(ev.currentTarget.querySelectorAll(".tab"));
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let j = -1;
    if (ev.key === "ArrowRight" || ev.key === "ArrowDown") j = (i + 1) % tabs.length;
    else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") j = (i - 1 + tabs.length) % tabs.length;
    else if (ev.key === "Home") j = 0;
    else if (ev.key === "End") j = tabs.length - 1;
    if (j < 0) return;
    ev.preventDefault(); select(j); tabs[j].focus();
  }
</script>
<!-- svelte-ignore a11y_interactive_supports_focus -->
{#if groups.length}<div class="section" id="what-s-new-in-this-release" use:reveal><SectionHead title="What's new in this release" count={total} note="from the release notes" id="what-s-new-in-this-release" /><div class="tabs" role="tablist" aria-label="Release changes" onkeydown={keydown}>{#each groups as g, i (g.key)}<button class="tab" type="button" role="tab" data-k={g.key} aria-selected={i === active ? "true" : "false"} tabindex={i === active ? 0 : -1} onclick={() => select(i)}>{g.icon + " " + g.label}<span class="cnt">{String(g.items.length)}</span></button>{/each}</div><div class="panel" use:stagger>{#if groups[active]}{#each groups[active].items as it}{@const sub = groups[active].sub(it)}<div class="change"><div class="ct"><Linkify text={it.title || ""} /></div>{#if sub}<div class="cm">{groups[active].key === "fixes" && it.verified ? "✓ " + sub : sub}</div>{/if}</div>{/each}{/if}</div></div>{/if}
