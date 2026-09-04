// Trends chart geometry — pure functions that turn the per-run timeline into drawing specs
// (Chart.svelte renders them). viewBox coordinates; CSS scales the width, height follows.
import { RISK } from "./tables.js";
import { verdictWord } from "./verdict.js";

export function geom() {
  const W = 340, H = 150, r = 10, b = 24;
  return { W, H, l: 30, t: 12, r: W - r, b: H - b };
}
export function px(c, i, n) { return c.l + (c.r - c.l) * (n <= 1 ? 0.5 : i / (n - 1)); }
export function py(c, v, vmax) { return c.b - (c.b - c.t) * Math.min(v, vmax) / (vmax || 1); }
export function maxOf(a) { return a.reduce((m, v) => (v > m ? v : m), 0); }

// Monotone-cubic (Fritsch–Carlson) interpolation, sampled densely: the curve passes through
// every run and never overshoots — a count can't dip below zero between two runs, and
// stacked bands can't cross (the caller also clamps them). Returns chart coordinates
// [[x,y],…]; x spacing is the run index, so the tangents use h = 1.
export function monotone(ys, c, vmax, steps) {
  const n = ys.length;
  let i;
  if (n < 2) return ys.map((v, k) => [px(c, k, n), py(c, v, vmax)]);
  const d = [], m = [];
  for (i = 0; i < n - 1; i++) d.push(ys[i + 1] - ys[i]);
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (i = 1; i < n - 1; i++) m[i] = (d[i - 1] * d[i] <= 0) ? 0 : (d[i - 1] + d[i]) / 2;
  for (i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], s2 = a * a + b * b;
    if (s2 > 9) { const t = 3 / Math.sqrt(s2); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
  }
  const out = [];
  for (i = 0; i < n - 1; i++) {
    for (let k = 0; k < steps; k++) {
      const u = k / steps, u2 = u * u, u3 = u2 * u;
      const y = (2 * u3 - 3 * u2 + 1) * ys[i] + (u3 - 2 * u2 + u) * m[i] + (-2 * u3 + 3 * u2) * ys[i + 1] + (u3 - u2) * m[i + 1];
      out.push([px(c, i + u, n), py(c, Math.max(0, y), vmax)]);
    }
  }
  out.push([px(c, n - 1, n), py(c, ys[n - 1], vmax)]);
  return out;
}
export function pathOf(pts) { return pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(""); }

// The ledger tracks at most issues_cap issues per version (server-fed; older payloads carry
// only the boolean, where the current list length IS the cap). A count series pinned there
// is SATURATION, not a real plateau — the charts mark it rather than read as a flat line.
export function issuesCap(data) {
  if (data.issues_cap) return data.issues_cap;
  return data.issues_capped ? (data.known_issues || []).length : 0;
}
// The last 30 runs, oldest first — or null when there aren't ≥3 real per-run points yet
// (the coarse all-approx fallback only ever drew near-empty charts).
export function prepTimeline(data) {
  let tl = (data.timeline || []).slice();
  if (!tl.length) return null;
  tl.sort((a, b) => String(a.t).localeCompare(String(b.t)));
  tl = tl.slice(-30);
  if (tl.filter((r) => !r.approx).length < 3) return null;
  return tl;
}

function grid(c, vmax, ticks, unit) {
  const out = [];
  for (let i = 0; i <= ticks; i++) {
    const v = vmax * i / ticks, y = c.b - (c.b - c.t) * i / ticks;
    out.push({ y, label: unit === "$" ? ("$" + v.toFixed(2)) : (unit === "s" ? (Math.round(v) + "s") : (unit === "%" ? (Math.round(v) + "%") : String(Math.round(v)))) });
  }
  return out;
}
// The tracking-cap line, styled as an annotation: a dashed rule with a small labelled tag.
function capOf(c, capv, vmax) {
  const y = py(c, capv, vmax), txt = "tracking cap (" + capv + ")", w = txt.length * 4.7 + 8;
  return { y, txt, w: w.toFixed(0), x: c.l + 3, tx: c.l + 7 };
}
function line(c, series, vmax, n, stroke, wd, fillArea, dash, layers) {
  const pts = monotone(series, c, vmax, 8), d = pathOf(pts);
  if (fillArea) layers.push({ k: "area", d: d + "L" + px(c, n - 1, n).toFixed(1) + " " + c.b + "L" + px(c, 0, n).toFixed(1) + " " + c.b + "Z", stroke });
  layers.push({ k: "path", d, stroke, wd: wd || 2, dash: dash || null });
  layers.push({ k: "end", cx: px(c, n - 1, n), cy: py(c, series[n - 1], vmax), fill: stroke });
}
const real = (tl) => tl.filter((r) => !(r.approx && !r.issues));

export function pressureSpec(tl, cap) {
  const p = real(tl);
  if (p.length < 2) return null;
  const iss = p.map((r) => r.issues || 0), reg = p.map((r) => r.regressions || 0);
  const vmax = Math.max(maxOf(iss.concat(reg)) * 1.15, 4), c = geom(), n = p.length;
  const atCap = cap && iss[n - 1] >= cap;
  const layers = [];
  line(c, iss, vmax, n, "var(--accent)", 2.5, true, null, layers);
  line(c, reg, vmax, n, "var(--orange)", 2, false, null, layers);
  const capFmt = (v) => (cap && v >= cap ? cap + "+" : String(v));
  return {
    c, grid: grid(c, vmax, 3), layers, cap: (cap && maxOf(iss) >= cap) ? capOf(c, cap, vmax) : null,
    legend: [["known issues" + (atCap ? " — " + cap + "+ at cap" : ""), "var(--accent)"], ["regressions", "var(--orange)"]],
    rows: p,
    series: [{ name: "known issues", color: "var(--accent)", values: iss, vmax, fmt: capFmt },
             { name: "regressions", color: "var(--orange)", values: reg, vmax }],
  };
}
export function severitySpec(tl, cap) {
  const p = real(tl);
  if (p.length < 2) return null;
  const n = p.length, bands = [["low", "var(--gray)"], ["medium", "var(--warn)"], ["high", "var(--orange)"], ["critical", "var(--bad)"]];
  const tot = p.map((r) => r.issues || (r.critical + r.high + r.medium + r.low));
  const vmax = Math.max(maxOf(tot) * 1.1, 4), c = geom();
  // Stacked bands, each boundary smoothed then clamped to the one below so they never cross.
  let cum = p.map(() => 0), below = null;
  const layers = [], series = [];
  bands.forEach((band) => {
    const top = p.map((r, i) => cum[i] + (r[band[0]] || 0));
    let up = monotone(top, c, vmax, 8);
    if (below) up = up.map((pt, i) => [pt[0], Math.min(pt[1], below[i][1])]);
    const base = below ? below.slice().reverse() : [[px(c, n - 1, n), c.b], [px(c, 0, n), c.b]];
    layers.push({ k: "poly", d: pathOf(up) + "L" + base.map((pt) => pt[0].toFixed(1) + " " + pt[1].toFixed(1)).join("L") + "Z", fill: band[1] });
    series.push({ name: band[0], color: band[1], values: p.map((r) => r[band[0]] || 0), vmax });
    cum = top; below = up;
  });
  return {
    c, grid: grid(c, vmax, 3), layers, cap: (cap && maxOf(tot) >= cap) ? capOf(c, cap, vmax) : null,
    legend: [["critical", "var(--bad)"], ["high", "var(--orange)"], ["medium", "var(--warn)"], ["low", "var(--gray)"]],
    rows: p, series: series.reverse(),
  };
}
export function verdictSpec(tl) {
  const p = real(tl);
  if (p.length < 2) return null;
  const n = p.length, c = geom(), layers = [];
  [[0, 35, "var(--good)", "update"], [35, 70, "var(--warn)", "care"], [70, 100, "var(--bad)", "skip"]].forEach((bd) => {
    const y1 = py(c, bd[1], 100), y2 = py(c, bd[0], 100);
    layers.push({ k: "rect", x: c.l, y: y1, w: c.r - c.l, h: y2 - y1, fill: bd[2] });
    layers.push({ k: "text", x: c.r - 2, y: (y1 + y2) / 2 + 3, fill: bd[2], txt: bd[3] });
  });
  const risk = p.map((r) => RISK[r.recommendation] || 30), seq = [];
  for (let i = 0; i < n; i++) { if (i) seq.push(px(c, i, n) + "," + py(c, risk[i - 1], 100)); seq.push(px(c, i, n) + "," + py(c, risk[i], 100)); }
  layers.push({ k: "steps", points: seq.join(" ") });
  for (let j = 1; j < n; j++) { if (p[j].version !== p[j - 1].version) layers.push({ k: "vline", x: px(c, j, n) }); }
  layers.push({ k: "end", cx: px(c, n - 1, n), cy: py(c, risk[n - 1], 100), fill: "var(--accent)" });
  return {
    c, grid: [], layers, cap: null,
    legend: [["risk per run", "var(--accent)"], ["new version", "var(--accent2)"]],
    rows: p, series: [{ name: "verdict", color: "var(--accent)", values: risk, vmax: 100, fmt: (v, i) => verdictWord(p[i].recommendation) }],
  };
}
export function shareSpec(tl) {
  // What fraction of known issues are post-release regressions (vs pre-existing)?
  const p = tl.filter((r) => !(r.approx && !r.issues) && (r.issues || 0) > 0);
  if (p.length < 2) return null;
  const n = p.length, share = p.map((r) => Math.round(100 * (r.regressions || 0) / (r.issues || 1)));
  const c = geom(), layers = [];
  line(c, share, 100, n, "var(--orange)", 2.5, true, null, layers);
  return {
    c, grid: grid(c, 100, 4, "%"), layers, cap: null,
    legend: [["regressions ÷ known issues — " + share[n - 1] + "%", "var(--orange)"]],
    rows: p, series: [{ name: "regression share", color: "var(--orange)", values: share, vmax: 100, fmt: (v) => v + "%" }],
  };
}
