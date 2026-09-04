<script>
  // Known issues: critical first, two INDEPENDENT filter dimensions that combine (category ×
  // subsystem), a cap of 15 rows lifted by "Show all". The filter state lives here, so a stack
  // toggle re-derives the rows without resetting the filters.
  import { flushSync } from "svelte";
  import { app, stackActive } from "../lib/state.svelte.js";
  import { CAT, COMP, COMP_LABEL } from "../lib/tables.js";
  import { issueHitsStack, issueComponents } from "../lib/verdict.js";
  import { cap } from "../lib/fmt.js";
  import { announce, reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Icon from "./Icon.svelte";
  import IssueCard from "./IssueCard.svelte";

  const CAP = 15;
  const D = $derived(app.data);
  const ki = $derived.by(() => {
    const arr = (D.known_issues || []).slice();
    const order = { critical: 0, high: 1, medium: 2, low: 3 };
    const catOrder = { regression: 0, post_release: 1, diamond_lobster: 2, active: 3 };
    const rank = (m, k) => ((k in m) ? m[k] : 9);   // NB: 0 is falsy, don't use ||
    arr.sort((a, b) => {
      const s = rank(order, a.severity) - rank(order, b.severity);
      if (s) return s;
      const c = rank(catOrder, a.category) - rank(catOrder, b.category);
      if (c) return c;
      return (Number(b.weight) || 0) - (Number(a.weight) || 0);   // importance weight within severity × category
    });
    return arr;
  });
  const newCount = $derived(ki.filter((i) => i.is_new).length);
  const note = $derived((newCount ? ("🆕 " + newCount + " new since last run · ") : "") + (D.issues_capped ? "highest-ranked · " : "") + "critical first");
  // Prevalence/velocity context: the list is the severity-seeking top-N of a very large
  // install base — say so, and show how fast upstream resolves what we track.
  const calBits = $derived.by(() => {
    const cal = D.calibration || {}, b = [];
    if (cal.npm_weekly_downloads) {
      const dl = cal.npm_weekly_downloads;
      b.push("tracked against ~" + (dl >= 1e6 ? (dl / 1e6).toFixed(1) + "M" : dl.toLocaleString()) + " weekly npm installs");
    }
    if (cal.tracked_total && cal.closed_completed != null) b.push(cal.closed_completed + " of " + cal.tracked_total + " issues ever tracked for this release already fixed upstream");
    return b;
  });
  const cats = $derived.by(() => { const c = {}; ki.forEach((i) => { if (i.category) c[i.category] = (c[i.category] || 0) + 1; }); return c; });
  const compCounts = $derived.by(() => { const c = {}; ki.forEach((i) => { issueComponents(i).forEach((k) => { c[k] = (c[k] || 0) + 1; }); }); return c; });
  const compKeys = $derived(COMP.map((c) => c[0]).filter((k) => compCounts[k]));
  const active = $derived(stackActive());
  const mineN = $derived(ki.filter((i) => issueHitsStack(i)).length);

  let showAll = $state(false);
  let catFilter = $state("all");
  let compFilter = $state(null);
  // A vanished "My stack" tab after clear-all safely falls back to "All".
  $effect(() => { if (catFilter === "mine" && !active) catFilter = "all"; });
  const rows = $derived.by(() => {
    let shown = 0;
    return ki.map((i) => {
      const mine = issueHitsStack(i);
      const catOk = catFilter === "all" ? true : (catFilter === "mine" ? mine : (i.category || "other") === catFilter);
      const compOk = !compFilter || issueComponents(i).indexOf(compFilter) >= 0;
      const match = catOk && compOk;
      let vis = false;
      if (match) { vis = showAll || shown < CAP; if (vis) shown++; }
      return { i, mine, match, vis };
    });
  });
  const matched = $derived(rows.filter((r) => r.match).length);
  // Real user clicks only announce (ev.isTrusted) — synthetic clicks would talk over the setup announcement.
  function setCat(val, ev) { catFilter = val; showAll = false; flushSync(); if (ev.isTrusted) announce(matched + " of " + ki.length + " issues shown"); }
  function toggleComp(k, ev) { compFilter = (compFilter === k) ? null : k; showAll = false; flushSync(); if (ev.isTrusted) announce(matched + " of " + ki.length + " issues shown"); }
  function more(ev) { showAll = !showAll; flushSync(); if (ev.isTrusted) announce(showAll ? ("Showing all " + matched + " issues") : "List collapsed"); }
</script>
<div class="section" id="issues" use:reveal><SectionHead title="Known issues" count={ki.length + (D.issues_capped ? "+" : "")} {note} id="issues" />{#if calBits.length}<p class="cal-note">{calBits.join(" · ")}</p>{/if}<div class="ltabs ki-cats" role="group" aria-label="Filter known issues by category"><button class="ltab" type="button" aria-pressed={catFilter === "all" ? "true" : "false"} data-f="all" onclick={(e) => setCat("all", e)}>{"All (" + ki.length + ")"}</button>{#each Object.keys(cats) as c}{@const meta = CAT[c] || { icon: "doc", label: cap(c) }}<button class="ltab" type="button" aria-pressed={catFilter === c ? "true" : "false"} data-f={c} onclick={(e) => setCat(c, e)}><Icon k={meta.icon} size={13} />{" " + meta.label + " (" + cats[c] + ")"}</button>{/each}{#if active}<button class="ltab" type="button" aria-pressed={catFilter === "mine" ? "true" : "false"} data-f="mine" onclick={(e) => setCat("mine", e)}>{"★ My stack (" + mineN + ")"}</button>{/if}</div>{#if compKeys.length}<div class="filters ki-subs"><span class="fsep">subsystem</span>{#each compKeys as k}{@const m = COMP_LABEL[k]}<button class="fbtn fcomp" type="button" aria-pressed={compFilter === k ? "true" : "false"} data-f="comp:{k}" onclick={(e) => toggleComp(k, e)}><Icon k={m[0]} size={12} />{m[1] + " (" + compCounts[k] + ")"}</button>{/each}</div>{/if}<div class="issue-list">{#each rows as r (r.i.number)}<IssueCard i={r.i} mine={r.mine} visible={r.vis} />{/each}</div><button class="ki-more" type="button" style:display={matched > CAP ? "" : "none"} onclick={more}>{matched > CAP ? (showAll ? "Show fewer ▴" : "Show all " + matched + " ▾") : ""}</button></div>
