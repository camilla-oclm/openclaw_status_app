// Boot: the header's theme toggle and section nav (static HTML, plain DOM), the visitor's
// stack, then the Svelte app mounted into #app, the deep-link hash, the runtime refresh from
// latest.json, and the read-only test hook for the browser suites.
import { mount } from "svelte";
import App from "./App.svelte";
import { app, bootStack, setStack } from "./lib/state.svelte.js";
import { setupVerdict, setupBlockers, keyVerdict } from "./lib/verdict.js";
import { registry } from "./lib/dom.js";
import { ICONS } from "./lib/tables.js";

// DATA comes from the inlined assessment-data JSON block (the build-time copy, always present)
// and, when reachable, a runtime fetch('latest.json'): the page renders from the inline copy
// instantly, then re-renders if latest.json brings fresher data — so a data refresh doesn't
// need a full HTML rebuild, while offline / file:// viewing still works from inline.
function parseInline() {
  try { return JSON.parse(document.getElementById("assessment-data").textContent); }
  catch (e) { return null; }
}

// A stroke icon as a DOM node, for the one element outside the app: the theme button.
function iconEl(key, size) {
  const NS = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(NS, "svg");
  const attrs = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
                  "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
                  class: "ic-svg", "aria-hidden": "true", focusable: "false" };
  for (const k in attrs) s.setAttribute(k, attrs[k]);
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", ICONS[key] || ICONS.doc);
  s.appendChild(p);
  return s;
}

function initTheme() {
  const btn = document.getElementById("theme-toggle");
  let saved = null;
  try { saved = localStorage.getItem("oc-theme"); } catch (e) {}
  const prefersLight = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    btn.textContent = "";
    btn.appendChild(iconEl(t === "light" ? "moon" : "sun", 16));
    // keep the browser-chrome color in step with the manual toggle
    const mc = document.querySelector('meta[name="theme-color"]');
    if (mc) mc.setAttribute("content", t === "light" ? "#f3f5f9" : "#070a0f");
  }
  apply(saved || (prefersLight ? "light" : "dark"));
  btn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    apply(next);
    try { localStorage.setItem("oc-theme", next); } catch (e) {}
  });
}

// Header section nav: mark the section currently under the header. Scroll-driven
// (rAF-throttled) so the answer link stays lit while the hero is on screen.
function initNav() {
  const links = Array.from(document.querySelectorAll(".topnav a[data-nav]"));
  if (!links.length) return;
  let ticking = false;
  function update() {
    ticking = false;
    const line = 96;
    let cur = links[0];
    links.forEach((a) => {
      const t = document.getElementById(a.getAttribute("data-nav"));
      if (t && t.getBoundingClientRect().top <= line) cur = a;
    });
    links.forEach((a) => { a.classList.toggle("on", a === cur); });
  }
  window.addEventListener("scroll", () => {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
}

// Honor a deep-link hash once the sections exist: open the evidence and the tab that holds
// the target, then scroll to it.
function scrollToHash() {
  if (!location.hash || location.hash.length < 2) return;
  let t = null;
  try { t = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (e) {}
  if (!t) return;
  if (registry.openDetails && t.closest && t.closest("#details-body")) registry.openDetails();
  const panel = t.closest ? t.closest(".ltab-panel") : null;
  if (panel && registry.activateLongTab) registry.activateLongTab(panel);
  t.scrollIntoView({ behavior: "smooth", block: "start" });
  t.classList.add("tgt"); setTimeout(() => { t.classList.remove("tgt"); }, 1800);
}

initTheme();
initNav();
bootStack();
app.data = parseInline();
const target = document.getElementById("app");
target.textContent = "";   // the server-rendered fallback (render._seo_body) makes way for the app
mount(App, { target });
if (location.hash) setTimeout(scrollToHash, 60);

// Progressive enhancement: when latest.json is reachable (served pages, not file://),
// refresh from it. Pure addition — any failure leaves the inline data on screen. A stale
// (e.g. intermediary-cached) copy never downgrades a fresher inline one, and the same run
// isn't swapped in (that would only reset open tabs and expanded rows for zero change).
if (window.fetch) {
  fetch("latest.json", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
    .then((d) => {
      if (!d || !d.recommendation) return;
      const cur = (app.data && app.data.assessed_at) ? Date.parse(app.data.assessed_at) : 0;
      const nxt = d.assessed_at ? Date.parse(d.assessed_at) : 0;
      if (cur && nxt && nxt <= cur) return;
      app.data = d;
      if (location.hash) setTimeout(scrollToHash, 60);
    })
    .catch(() => { /* keep the inlined data */ });
}

// Test hook (pure reads only): lets a headless test exercise the REAL per-setup verdict logic
// — see tests/browser/per_setup_verdict.test.js — instead of a re-implementation that could
// drift. No effect on the page; exposes nothing not already derivable from the data.
try { window.__perSetupTest = { setupVerdict, setupBlockers, keyVerdict, setStack }; } catch (e) {}
