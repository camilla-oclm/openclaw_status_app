// Formatting helpers — pure functions, no DOM.
import { REPO } from "./tables.js";

export function issueUrl(n) { return "https://github.com/openclaw/openclaw/issues/" + n; }

// Untrusted hrefs (release URLs from the payload) go through this before use.
export function safeUrl(u) {
  if (!u) return "";
  const s = String(u).trim();
  if (/^(javascript|data|vbscript):/i.test(s)) return "";
  return s;
}

// Prefilled new-issue URL: the report lands with the exact page state (version, verdict,
// assessed-at) already filled in, so "this looks wrong" is actionable.
export function reportUrl(data) {
  const title = "[report] v" + (data.version || "?") + " " + (data.recommendation || "") + " — something looks wrong";
  const body = "Page: https://clawstat.us/ · assessed " + (data.assessed_at || "?") + "\n"
    + "Version assessed: v" + (data.version || "?") + " · verdict shown: " + (data.recommendation || "?") + "\n\n"
    + "**What looks wrong?** (a mis-tagged issue, a wrong severity, a verdict that doesn't match the evidence, a page bug...)\n\n";
  return REPO + "/issues/new?title=" + encodeURIComponent(title) + "&body=" + encodeURIComponent(body);
}

// "#91035" inside prose → [{t:"text"} | {a:"91035"}] segments (Linkify.svelte draws them).
export function linkParts(text) {
  const out = [], re = /#(\d{3,})/g;
  let last = 0, m;
  text = String(text || "");
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ t: text.slice(last, m.index) });
    out.push({ a: m[1] });
    last = re.lastIndex;
  }
  if (last < text.length) out.push({ t: text.slice(last) });
  return out;
}

export function timeAgo(iso) {
  const t = new Date(iso); if (isNaN(t)) return "";
  let s = (Date.now() - t.getTime()) / 1000;
  if (s < 0) s = 0;
  const d = Math.floor(s / 86400);
  if (d >= 1) return d + (d === 1 ? " day ago" : " days ago");
  const h = Math.floor(s / 3600); if (h >= 1) return h + "h ago";
  const mn = Math.floor(s / 60); return mn + "m ago";
}
export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso); if (isNaN(d)) return String(iso).slice(0, 10);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
export function cap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
export function whenText(d) { return (d == null || d === 0) ? "today" : (d === 1 ? "yesterday" : d + " days ago"); }
export function issueWord(n) { return n + (n === 1 ? " issue" : " issues"); }
export function listNames(entries) {
  const names = entries.map((e) => e.label);
  if (names.length > 4) return names.slice(0, 3).join(", ") + " and " + (names.length - 3) + " more";
  if (names.length <= 1) return names.join("");
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}
export function slugify(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "section"; }
