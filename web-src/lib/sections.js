// Which sections exist for a payload, and the aggregations the Impact section draws.
// The predicates mirror the old builders' "return null" conditions, so the tab strip and
// the evidence body carry exactly the sections they used to.
import { PLAT, COMP_LABEL, IMPACT, SEVW } from "./tables.js";
import { issueComponents, platformIssues, worstSeverity } from "./verdict.js";
import { prepTimeline } from "./charts.js";
import { cap as capWord } from "./fmt.js";

export function hasThesis(d) { return !!d.thesis; }
export function evidenceCards(d) {
  const e = d.evidence || {};
  return [
    { title: "✓ Reasons to update", items: e.for_updating, cls: "ev-for" },
    { title: "✕ Reasons to hold off", items: e.against_updating, cls: "ev-against" },
    { title: "Context", items: e.neutral, cls: "ev-neutral" },
  ].filter((c) => c.items && c.items.length);
}
export function flipList(d) { return (d.flip_conditions || []).filter((c) => String(c || "").trim()); }
export function hasIssues(d) { return (d.known_issues || []).length > 0; }
export function hasReco(d) { return !!d.recommended_version && typeof d.recommended_version === "object"; }
export function hasTriage(d) { return !!((d.clawsweeper_work || []).length || (d.clawsweeper_closed || []).length); }
// Features first (the reason a release exists); only categories that have items.
export function changeGroups(d) {
  const ch = d.changes || {};
  return [
    { key: "features", icon: "✨", label: "Highlights", items: ch.features || [], sub: (x) => x.value || "" },
    { key: "fixes", icon: "✓", label: "Fixes", items: ch.fixes || [], sub: (x) => (x.verified ? "verified" : "") },
    { key: "breaking", icon: "⚠", label: "Breaking", items: ch.breaking || [], sub: (x) => x.impact || "" },
  ].filter((g) => g.items.length);
}
export function releaseHistory(d) {
  const rh = (d.release_history || []).filter((r) => r.version);
  rh.sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)));   // newest first
  return rh;
}
export function hasCatchup(d) { return releaseHistory(d).length >= 2; }
export function hasTrends(d) { return !!prepTimeline(d); }
export function hasTrackRecord(d) { return ((d.track_record || {}).versions || []).length > 0; }
export function hasHistory(d) { return (d.version_history || []).length > 0; }

// Component health — which subsystems this release hits. Bar length = issue volume,
// colour = worst severity. Null when nothing is tagged.
export function componentAgg(d, stack) {
  const ki = d.known_issues || [], agg = {};
  ki.forEach((i) => {
    const sv = String(i.severity || "low").toLowerCase(), w = SEVW[sv] || 0;
    issueComponents(i).forEach((c) => {
      const a = agg[c] || (agg[c] = { count: 0, regr: 0, worst: "low", w: 0 });
      a.count++;
      if (i.category === "regression") a.regr++;
      if (w > a.w) { a.w = w; a.worst = sv; }
    });
  });
  const keys = Object.keys(agg);
  if (!keys.length) return null;
  let maxCount = 0; keys.forEach((k) => { if (agg[k].count > maxCount) maxCount = agg[k].count; });
  keys.sort((a, b) => agg[b].count - agg[a].count || agg[b].w - agg[a].w);   // hot spots first
  return keys.map((k) => {
    const a = agg[k], meta = COMP_LABEL[k] || ["build", capWord(k)];
    return { key: k, ic: meta[0], name: meta[1], a, maxCount, mine: !!stack[k],
             label: a.count + (a.count === 1 ? " issue" : " issues") + (a.regr ? " · " + a.regr + " regr" : "") };
  });
}
// Platform / channel meters. Legacy payloads with no per-issue platform tags fall back to
// the analyst's platform_impact level. Null when there is nothing to show.
export function platformAgg(d, stack) {
  const ki = d.known_issues || [];
  const anyTagged = ki.some((i) => (i.platforms || []).length);
  const stat = {};
  let maxCount = 0;
  PLAT.forEach((p) => {
    const iss = platformIssues(p[0], ki);
    stat[p[0]] = { count: iss.length, regr: iss.filter((i) => i.category === "regression").length, worst: worstSeverity(iss).name };
    if (iss.length > maxCount) maxCount = iss.length;
  });
  const pi = d.platform_impact || {};
  if (!anyTagged) {
    if (!Object.keys(pi).length) return null;
    const LVLSEV = { high: "high", medium: "medium", low: "low", none: "none" };
    const LVLFILL = { high: 5, medium: 3, low: 2, none: 0 };
    PLAT.forEach((p) => {
      const lvl = String(pi[p[0]] || "none").toLowerCase();
      stat[p[0]] = { count: LVLFILL[lvl] || 0, regr: 0, worst: LVLSEV[lvl] || "none", lbl: (IMPACT[lvl] || IMPACT.none).label };
    });
    maxCount = 5;
  } else if (!maxCount) { return null; }
  // When every affected platform shows the same volume×severity, the bars carry no signal.
  const sigs = {};
  let nShown = 0;
  PLAT.forEach((p) => { const a = stat[p[0]]; if (a.count > 0) { nShown++; sigs[a.worst + ":" + Math.round(a.count / (maxCount || 1) * 10)] = 1; } });
  return {
    flat: nShown >= 2 && Object.keys(sigs).length === 1,
    rows: PLAT.map((p) => {
      const a = stat[p[0]];
      return { key: p[0], ic: p[1], name: p[2], a, maxCount, mine: !!stack[p[0]],
               label: a.lbl || (a.count ? a.count + (a.count === 1 ? " issue" : " issues") : "clear") };
    }),
  };
}
// The meter fill, floored at 6% so a 1-issue row shows a visible nub.
export function fillPct(a, maxCount) { return (a.count ? Math.max(6, Math.round(a.count / (maxCount || 1) * 100)) : 0) + "%"; }
