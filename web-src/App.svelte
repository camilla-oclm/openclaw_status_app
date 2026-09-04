<script>
  // The page body, in order: the answer (status word + one line + the per-platform strip that
  // doubles as the setup picker) → the best version to run today → WHY in two cards → what
  // would change the verdict → everything else behind one "full evidence" toggle → footer.
  import { app } from "./lib/state.svelte.js";
  import { statusOf } from "./lib/verdict.js";
  import { hasReco, flipList } from "./lib/sections.js";
  import { beginReveal } from "./lib/dom.js";
  import Hero from "./components/Hero.svelte";
  import Reco from "./components/Reco.svelte";
  import Why from "./components/Why.svelte";
  import Flip from "./components/Flip.svelte";
  import Details from "./components/Details.svelte";
  import Footer from "./components/Footer.svelte";

  const D = $derived(app.data);
  const ok = $derived(!!(D && D.recommendation));
  // Page-level status-tinted backdrop glow. A data-* attr + dedicated --pglow var, NOT a
  // .tone-* class: a tone class on body would cascade --tone into every component.
  $effect(() => {
    if (!ok) return;
    try { document.body.setAttribute("data-tone", statusOf().tone.replace("tone-", "")); } catch (e) {}
  });
  if (app.data && app.data.recommendation) {
    // The scroll reveal observes only this render pass; the entrance window (html.entering)
    // holds the bento row's reveal for the answer's animation during the first second.
    beginReveal();
    const root = document.documentElement;
    root.classList.add("entering");
    setTimeout(() => root.classList.remove("entering"), 1200);
  }
</script>
{#if !ok}<div class="errbox"><h2>Assessment unavailable</h2><p>The assessment data could not be loaded. The pipeline may not have run yet.</p></div>{:else}<Hero />{#if hasReco(D)}<Reco />{/if}<Why />{#if flipList(D).length}<Flip />{/if}<Details /><Footer />{/if}
