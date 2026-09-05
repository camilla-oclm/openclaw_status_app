<script>
  // Verdict track record (accountability: did the first read hold?). A released version is
  // immutable — its issue list only accrues — so an honest verdict should hold or harden
  // between runs; softening is rare and gets the loud badge.
  import { app } from "../lib/state.svelte.js";
  import { VERDICTS, TR_BADGE } from "../lib/tables.js";
  import { fmtDate } from "../lib/fmt.js";
  import { reveal, stagger } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  import Mark from "./Mark.svelte";
  const D = $derived(app.data);
  const tr = $derived(D.track_record || {});
  const vs = $derived(tr.versions || []);
  const sm = $derived(tr.summary || {});
  const multi = $derived((sm.held || 0) + (sm.hardened || 0) + (sm.softened || 0) + (sm.mixed || 0));
  const bits = $derived.by(() => {
    const b = [];
    if (sm.held) b.push(sm.held + " held from the first read");
    if (sm.hardened) b.push(sm.hardened + " hardened as reports accrued");
    if (sm.softened) b.push(sm.softened + " softened");
    if (sm.mixed) b.push(sm.mixed + " moved and returned");
    return b;
  });
  function dates(e) {
    let d = (e.first && e.first.t ? fmtDate(e.first.t) : "");
    if (e.runs > 1 && e.last && e.last.t) d += " → " + fmtDate(e.last.t);
    return d;
  }
  const badge = (e) => TR_BADGE[e.direction] || TR_BADGE.single;
  const toneOf = (pv) => (VERDICTS[pv] || {}).tone || "tone-muted";
</script>
{#if vs.length}<div class="section" id="track-record" use:reveal><SectionHead title="Verdict track record" note="did the first read hold?" id="track-record" />{#if multi >= 1}<div class="tr-sum">{"Across " + multi + " version" + (multi === 1 ? "" : "s") + " with repeat assessments: " + bits.join(" · ") + "."}</div>{/if}<div class="tr-list" use:stagger>{#each vs as e}{@const b = badge(e)}<div class="tr-row{e.current ? ' cur' : ''}"><span class="tr-ver">v{e.version}{#if e.current}<span class="cur-dot" title="the current release">●</span>{/if}</span><span class="tr-path" title="Verdict sequence across this version's assessments">{#each e.path || [] as pv, idx}{#if idx}<span class="tr-arrow" aria-hidden="true">→</span>{/if}<span class="vgw {toneOf(pv)}"><Mark rec={pv} size={14} /></span>{/each}</span><span class="tr-runs">{e.runs + (e.runs === 1 ? " run" : " runs")}</span><span class="tr-badge{b.cls}" title={b.tip}>{b.txt}</span><span class="tr-dates">{dates(e)}</span></div>{/each}</div><div class="tr-note">Why this matters: a released version is immutable, so its issue list only accrues — an honest verdict should hold or harden between runs. This table is the receipts for the flip-conditions above: when one fires, the move shows up here.</div></div>{/if}
