<script>
  // Impact — the per-component verdict line leads (the decision-shaped view), then component
  // meters (hot spots), then platform/channel meters. Bar = issue volume, colour = worst severity.
  import { app } from "../lib/state.svelte.js";
  import { COMP, VERDICTS } from "../lib/tables.js";
  import { componentIssues, keyVerdict } from "../lib/verdict.js";
  import { componentAgg, platformAgg, fillPct } from "../lib/sections.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Icon from "./Icon.svelte";
  import Mark from "./Mark.svelte";

  const D = $derived(app.data);
  const comp = $derived(componentAgg(D, app.stack));
  const plat = $derived(platformAgg(D, app.stack));
  // The conservative per-setup machinery run for each subsystem alone: a component carrying
  // a blocker keeps the global verdict, the others ease one notch. CLIENT-ONLY, never in SSR.
  const entries = $derived.by(() => {
    const ki = D.known_issues || [], es = [];
    COMP.forEach((c) => {
      const n = componentIssues(c[0], ki).length;
      if (!n) return;
      es.push({ ic: c[1], name: c[2], n, kv: keyVerdict([], [c[0]]) });
    });
    es.sort((a, b) => (b.kv.blockers.length ? 1 : 0) - (a.kv.blockers.length ? 1 : 0) || b.n - a.n);
    return es;
  });
  const clear = $derived(COMP.length - entries.length);
  const lab = (e) => (VERDICTS[e.kv.rec] || { label: String(e.kv.rec || "") }).label;
  function why(e) {
    const n = e.kv.blockers.length;
    return n ? n + " blocking issue" + (n === 1 ? "" : "s") + " (version-confirmed high/critical with corroboration or community traction) land" + (n === 1 ? "s" : "") + " here — the global verdict applies squarely."
      : (e.kv.softened ? "No blocking issue is confirmed to land here, so the global verdict eases one notch for this subsystem." : "Matches the global verdict.");
  }
</script>
{#snippet meter(r)}<div class="plat sv-{r.a.count ? r.a.worst : 'none'}{r.mine ? ' you' : ''}"><div class="ph"><span class="pn"><Icon k={r.ic} size={13} /><span class="pname">{r.name}</span>{#if r.mine}<span class="me">{" ★you"}</span>{/if}</span></div><div class="pmeter"><span class="vbar"><i class="vfill" style="width:{fillPct(r.a, r.maxCount)}"></i></span><span class="pl">{r.label}</span></div></div>{/snippet}
{#if comp || plat}<div class="section" id="impact" use:reveal><SectionHead title="Impact" note="bar = issue volume · colour = worst severity" id="impact" />{#if entries.length}<div class="impact-sub" id="verdict-line"><div class="impact-sublabel">Verdict by component</div><div class="vline" role="list" aria-label="Per-component verdict">{#each entries as e}<span class="vchip{e.kv.blockers.length ? ' hot' : ''}" role="listitem" aria-label="{e.name}: {lab(e)}, {e.n} issue{e.n === 1 ? '' : 's'}" title="{e.name}: {lab(e)} — {why(e)} Derived on this page from the issue tags; the official verdict stays global."><span class="vc-em" aria-hidden="true"><Mark rec={e.kv.rec} /></span><Icon k={e.ic} size={12} /><span class="vc-name">{e.name}</span><span class="vc-w" aria-hidden="true">{lab(e)}</span><span class="vc-n">{String(e.n)}</span></span>{/each}</div>{#if clear > 0}<div class="vline-note">{clear} other component{clear === 1 ? " has" : "s have"} no tracked issues this release.</div>{/if}</div>{/if}{#if comp}<div class="impact-sub" id="components"><div class="impact-sublabel">By component</div><div class="plat-grid">{#each comp as r (r.key)}{@render meter(r)}{/each}</div></div>{/if}{#if plat}<div class="impact-sub" id="platforms"><div class="impact-sublabel">By platform &amp; channel</div>{#if plat.flat}<div class="impact-note">Affects all platforms about equally — the meaningful split is by component, above.</div>{/if}<div class="plat-grid">{#each plat.rows as r (r.key)}{@render meter(r)}{/each}</div></div>{/if}</div>{/if}
