// The verdict machinery — the client twin of verdict.py's evidence gate, the per-setup
// refinement, the plain-language status and the sentences built from them. Every function
// reads the reactive state, so a component that calls one inside a $derived re-runs when
// the payload or the stack changes.
import { app, stackActive, stackPlatforms, stackComponents } from "./state.svelte.js";
import { VERDICTS, VERDICT_ORDER, PLAT, PLAT_LABEL, COMP, COMP_LABEL, SEVW, STACK_MATCH, PLAT_KEYS,
         CONF_DESC, CONF_DESC_FRESH } from "./tables.js";
import { whenText, listNames } from "./fmt.js";

// Components come pre-derived in DATA (render side); just normalize to known keys.
export function issueComponents(i) {
  const raw = (i && i.components) || [], out = [];
  if (Array.isArray(raw)) raw.forEach((c) => {
    c = String(c || "").toLowerCase(); if (COMP_LABEL[c] && out.indexOf(c) < 0) out.push(c);
  });
  return out;
}
// Surfaces an issue hits: prefer the analyst's structured `platforms`; if absent
// (pre-structured assessments), fall back to the title keyword heuristic.
export function issuePlatforms(i) {
  const out = [], raw = i && i.platforms;
  if (Array.isArray(raw)) {
    raw.forEach((p) => { p = String(p || "").toLowerCase(); if (PLAT_KEYS[p] && out.indexOf(p) < 0) out.push(p); });
  }
  if (out.length) return out;
  const title = String((i && i.title) || "");
  for (const k in STACK_MATCH) { if (STACK_MATCH[k].test(title)) out.push(k); }
  return out;
}
export function worstSeverity(issues) {
  let w = 0, name = "none";
  issues.forEach((it) => {
    const s = String((it && it.severity) || "low").toLowerCase(), sw = SEVW[s] || 0;
    if (sw > w) { w = sw; name = s; }
  });
  return { name, w };
}
// Issues hitting a platform = its own tag OR a cross-cutting "all" issue.
export function platformIssues(k, ki) {
  return ki.filter((i) => { const ps = issuePlatforms(i); return ps.indexOf(k) >= 0 || ps.indexOf("all") >= 0; });
}
export function componentIssues(k, ki) { return ki.filter((i) => issueComponents(i).indexOf(k) >= 0); }

// Does this issue touch the given platform/component key sets? The shared core of the
// per-setup verdict AND the per-component line.
export function hitsKeys(i, plats, comps) {
  const ps = issuePlatforms(i);
  if (plats.length) {
    if (ps.indexOf("all") >= 0) return true;
    for (let j = 0; j < plats.length; j++) { if (ps.indexOf(plats[j]) >= 0) return true; }
  }
  const cs = issueComponents(i);
  for (let m = 0; m < comps.length; m++) { if (cs.indexOf(comps[m]) >= 0) return true; }
  return false;
}
export function issueHitsStack(i) {
  if (!stackActive()) return false;
  return hitsKeys(i, stackPlatforms(), stackComponents());
}

// ── per-setup verdict ─────────────────────────────────────────────────────────
// A CONSERVATIVE, fail-closed refinement of the global verdict for the user's stack: it
// softens by AT MOST one step, and ONLY when no BLOCKING issue touches them. Fresh releases
// never soften; the result is never harsher than the global verdict. latest.json / badge /
// RSS / SSR keep the canonical GLOBAL verdict — this refinement is page-only.
//
// What counts as a BLOCKER is calibrated: two independent evidence gates, each failing
// CLOSED when its field is absent — severity trust (a HIGH needs a human behind its priority
// or community traction; criticals always block what they touch) and breadth (pinning EVERY
// stack must be earned by megathread-class engagement).
export function humanBacked(i) {
  const p = i.priority_provenance;
  if (p == null || p === "") return true;      // field absent (pre-2026-07 payload) → trust it
  return String(p).toLowerCase() !== "bot";    // human / bot-corroborated / unknown all count
}
export function impactAtLeast(i, lvl) {
  let imp = i.impact;
  if (imp == null || imp === "") return true;  // field absent → trust it (fail closed)
  imp = String(imp).toLowerCase();
  return lvl === "high" ? imp === "high" : imp !== "low";
}
// Like hitsKeys, but only EXPLICIT tags match — the cross-cutting "all" token doesn't.
export function hitsExplicit(i, plats, comps) {
  const ps = issuePlatforms(i), cs = issueComponents(i);
  for (let j = 0; j < plats.length; j++) { if (ps.indexOf(plats[j]) >= 0) return true; }
  for (let m = 0; m < comps.length; m++) { if (cs.indexOf(comps[m]) >= 0) return true; }
  return false;
}
export function isFresh() { const d = app.data; return !!(d && d.freshness && d.freshness.fresh); }
// Blocking issues for an arbitrary platform/component key set: high/critical AND
// version-confirmed (or fresh) AND evidenced (gates above) AND landing on the keys.
export function blockersFor(plats, comps) {
  const fresh = isFresh();
  return ((app.data && app.data.known_issues) || []).filter((i) => {
    const s = String(i.severity || "low").toLowerCase();
    if (s !== "high" && s !== "critical") return false;
    if (!i.affects_version && !fresh) return false;        // only version-confirmed counts
    // Gate 1 — severity trust.
    if (s === "high" && !humanBacked(i) && !impactAtLeast(i, "medium")) return false;
    // Cross-cutting = tagged "all", or the fail-closed no-platform guard class.
    const ps = issuePlatforms(i);
    const crossCut = ps.indexOf("all") >= 0 || (plats.length && ps.length === 0);
    if (crossCut) {
      // Gate 2 — breadth: earned → hits everyone; not earned → explicit tags only.
      if (impactAtLeast(i, "high")) return true;
      return hitsExplicit(i, plats, comps);
    }
    return hitsKeys(i, plats, comps);
  });
}
export function setupBlockers() { return blockersFor(stackPlatforms(), stackComponents()); }
// The per-setup machinery for an arbitrary key set — powers the per-component line and the
// tiles. Softens AT MOST one notch, never harsher, never ⏸️→✅, fresh never softens.
export function keyVerdict(plats, comps) {
  const fresh = isFresh();
  const gRec = app.data.recommendation, gIdx = VERDICT_ORDER.indexOf(gRec);
  const blockers = blockersFor(plats, comps);
  let rec = gRec, softened = false;
  if (!fresh && !blockers.length && gIdx > 0) { rec = VERDICT_ORDER[gIdx - 1]; softened = true; }
  return { rec, global: gRec, softened, fresh, blockers };
}
export function setupVerdict() {
  const fresh = isFresh();
  const gRec = app.data.recommendation, gIdx = VERDICT_ORDER.indexOf(gRec);
  const blockers = setupBlockers();
  let rec = gRec, softened = false;
  if (stackActive() && !fresh && !blockers.length && gIdx > 0) { rec = VERDICT_ORDER[gIdx - 1]; softened = true; }
  return { rec, global: gRec, softened, fresh, blockers };
}

export function confNote(conf) { return (isFresh() ? CONF_DESC_FRESH : CONF_DESC)[conf] || ""; }
export function verdictWord(rec) { return (VERDICTS[rec] || {}).label || String(rec || ""); }

// ── plain-language status ─────────────────────────────────────────────────────
// Mirrors verdict.status_for (Python): the three verdict words, or "Too new to call" while a
// non-skip release is still inside the fresh window (a fresh ⏸️ stays a skip).
export function statusOf() {
  const rec = app.data.recommendation, base = VERDICTS[rec];
  if (!base) return { key: "unknown", label: String(rec || "Assessed"), tone: "tone-muted", rec, early: null };
  if (isFresh() && rec !== "⏸️") return { key: "wait", label: "Too new to call", tone: "tone-info", rec, early: base.label };
  const key = rec === "✅" ? "update" : rec === "⚠️" ? "care" : "skip";
  return { key, label: base.label, tone: base.tone, rec, early: null };
}
// Per-platform verdicts — the conservative keyVerdict run for each surface alone.
export function platformVerdicts() {
  return PLAT.map((p) => ({ key: p[0], ic: p[1], label: p[2], kv: keyVerdict([p[0]], []) }));
}
export function stripSummary(pvs) {
  const gi = VERDICT_ORDER.indexOf(app.data.recommendation);
  return {
    clear: pvs.filter((e) => VERDICT_ORDER.indexOf(e.kv.rec) < gi),
    pinned: pvs.filter((e) => VERDICT_ORDER.indexOf(e.kv.rec) >= gi),
  };
}
// The credible blockers behind the verdict: the server-side evidence gate's numbers joined
// back onto known_issues; payloads predating the gate fall back to the client twin.
export function gateBlockers() {
  const ki = app.data.known_issues || [], byNum = {};
  ki.forEach((i) => { byNum[i.number] = i; });
  const eg = app.data.evidence_gate;
  let list = [];
  if (eg && Array.isArray(eg.blockers)) {
    eg.blockers.forEach((n) => { if (byNum[n]) list.push(byNum[n]); });
  } else {
    list = blockersFor(PLAT.map((p) => p[0]), COMP.map((c) => c[0]));
  }
  list.sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0));
  return list;
}
// One plain sentence under the status word — computed from the same facts the strip shows.
export function answerLine(st, pvs) {
  const D = app.data;
  const ver = "v" + (D.version || ""), fr = D.freshness || {}, pre = D.latest_prerelease || {};
  const bl = gateBlockers(), n = bl.length;
  if (st.key === "wait") {
    const spec = fr.version_specific_issues || 0;
    return ver + " came out " + whenText(fr.days_since_release) + ". Bug reports are still arriving"
      + (spec ? " — " + spec + " so far name this release" : "")
      + ", so this is an early read: " + st.early.toLowerCase() + ". Back up first, and check back in a day or two.";
  }
  if (st.key === "update") return "No credible blocking issue is confirmed for this release. Back up as usual, then update.";
  if (st.key === "skip") {
    let sk = "Open issues outweigh the benefits right now — stay on your current version.";
    if (pre.tag) sk += " A fix may be staged in " + pre.tag + " (pre-release); it isn't in this release yet.";
    return sk;
  }
  if (st.key !== "care") return "";
  if (!n) {
    const dr = ((D.evidence_gate || {}).departure || {}).reason;
    return "No credible blocking issue is confirmed for this release, but the analyst sees a contained risk"
      + (dr ? ": " + dr : "") + ". Back up first.";
  }
  const sm = stripSummary(pvs);
  const nTxt = n + " credible blocking issue" + (n === 1 ? "" : "s");
  if (sm.clear.length && sm.pinned.length) {
    const seen = {}; let landing = 0;
    sm.pinned.forEach((e) => { e.kv.blockers.forEach((b) => { if (!seen[b.number]) { seen[b.number] = 1; landing++; } }); });
    const others = sm.clear.length === 1 ? "the other platform is clear" : "the other " + sm.clear.length + " platforms and channels are clear";
    return "Safe for most setups. " + listNames(sm.pinned) + " users should wait — " + landing + " blocking issue" + (landing === 1 ? "" : "s")
      + " land" + (landing === 1 ? "s" : "") + " there; " + others + ". Back up first either way.";
  }
  if (sm.clear.length) return "No platform is pinned by a blocking issue, but " + nTxt + " remain" + (n === 1 ? "s" : "") + " open for this release — back up first.";
  return nTxt + " " + (n === 1 ? "is" : "are") + " confirmed for this release and can hit any setup. Back up first, or wait for the next release.";
}
// The sentence on the best version to run today (server-computed: verdict.recommended_version).
export function recoLine(rv) {
  const latest = rv.latest || {};
  if (!rv.version) {
    return "Every release we've assessed is either under " + (rv.min_days || 7) + " days old, rated skip, or "
      + "carries a widespread breaker. If you're on a version that works for you, stay put.";
  }
  const days = rv.age_days != null ? rv.age_days + " days in the field" : "settled in the field";
  let why = (rv.kind === "latest" ? "The latest release, and the most settled one we've assessed: " : "The most settled release we've assessed: ")
    + days + " with no widespread breaker.";
  if (rv.kind !== "latest") {
    why += " If you're on a newer version that works for you, stay put.";
    if (latest.version && latest.version !== rv.version) {
      if (latest.age_days != null && latest.age_days < (rv.min_days || 7))
        why += " The latest, v" + latest.version + ", came out " + whenText(latest.age_days) + " — too new to call settled.";
      else if (latest.recommendation)
        why += " The latest, v" + latest.version + ", is rated " + ((VERDICTS[latest.recommendation] || { label: "" }).label.toLowerCase()) + ".";
    }
  }
  return why;
}
