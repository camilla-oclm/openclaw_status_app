<script>
  // The full evidence, behind one toggle. Synchronous on purpose: the sections fade in through
  // .reveal (a view transition here would crossfade the whole viewport for a change below
  // the button and swallow clicks while it runs). Deep links open it through the registry.
  import { flushSync } from "svelte";
  import { app } from "../lib/state.svelte.js";
  import { announce, registry, revealAll } from "../lib/dom.js";
  import { hasThesis, evidenceCards, hasIssues } from "../lib/sections.js";
  import Stats from "./Stats.svelte";
  import Thesis from "./Thesis.svelte";
  import Evidence from "./Evidence.svelte";
  import LongTail from "./LongTail.svelte";
  import Issues from "./Issues.svelte";
  import About from "./About.svelte";

  const D = $derived(app.data);
  const bits = $derived(["the analyst's full reasoning", (D.known_issues || []).length + (D.issues_capped ? "+" : "") + " known issues with filters",
                         "impact by platform & component", "changelog", "trends", "past verdicts"]);
  let open = $state(false);
  let body;
  function setOpen(o) {
    open = o;
    flushSync();
    // sections inside a hidden wrapper never tripped the IntersectionObserver — reveal directly
    if (o) revealAll(body);
  }
  function toggle() {
    const was = open;
    setOpen(!was);
    announce(was ? "Full evidence hidden." : "Full evidence shown.");
  }
  registry.openDetails = () => { if (!open) setOpen(true); };
  $effect(() => () => { registry.openDetails = null; });
</script>
<div class="details" id="details">
  <button class="details-toggle" type="button" aria-expanded={open ? "true" : "false"} aria-controls="details-body" onclick={toggle}><span class="dt-main">{open ? "Hide the full evidence" : "Show the full evidence"}</span><span class="cx" aria-hidden="true">▾</span><span class="dt-sub">{bits.join(" · ")}</span></button>
  <div class="details-body" id="details-body" hidden={!open} bind:this={body}><Stats />{#if hasThesis(D)}<Thesis />{/if}{#if evidenceCards(D).length}<Evidence />{/if}<LongTail />{#if hasIssues(D)}<Issues />{/if}<About /></div>
</div>
