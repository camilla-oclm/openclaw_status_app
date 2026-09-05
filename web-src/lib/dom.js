// Small DOM-side helpers: the live-region announcer, the view-transition wrapper, the
// scroll reveal, and a registry through which main.js reaches into mounted components
// (deep links need to open the evidence and the tab that holds the target).

// Announce a state change to assistive tech via the body-level polite live region.
// Clear-then-set (with a beat in between) so repeating the same text re-announces.
export function announce(msg) {
  const r = document.getElementById("live");
  if (!r) return;
  r.textContent = "";
  setTimeout(() => { r.textContent = msg; }, 30);
}

// View Transitions (the long-tail tab switch): used only where the API exists and motion
// isn't reduced; the initial activation and deep links take the synchronous path.
export function withTransition(apply, instant) {
  const ok = !instant && typeof document.startViewTransition === "function" &&
    !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  if (!ok) { apply(); return; }
  try { document.startViewTransition(apply); } catch (e) { apply(); }
}

export const registry = { openDetails: null, activateLongTab: null };

// ── scroll reveal ─────────────────────────────────────────────────────────────
// Sections fade in on their first visible pixel, and the items inside a [data-stagger]
// container (tiles, cards, rows, list items, paragraphs) arrive one by one in document
// order, 40 ms apart — the page hands its information over at a reading pace as the
// visitor scrolls. One IntersectionObserver serves both (threshold:0 — a fractional
// threshold is unreachable for very tall sections on a phone).
//
// Nothing is gated on JS animation: without IntersectionObserver everything shows at once
// (html.no-io); anything on screen that the observer missed is revealed by a sweep 1.8 s
// after render and after every scroll or resize; a focused element reveals its own row;
// printing reveals the whole page. Reduced motion turns the whole thing off in CSS.
let io = null, wired = false, scrollTimer = 0;
const STEP = 40, CAP = 8;
function show(n) { if (io) io.unobserve(n); n.classList.add("in"); }
function onIntersect(entries) {
  const entering = entries.filter((e) => e.isIntersecting).map((e) => e.target);
  entering.sort((a, b) => ((a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1));
  let k = 0;
  entering.forEach((n) => {
    if (n.hasAttribute("data-stagger-item")) n.style.setProperty("--d", Math.min(k++, CAP) * STEP + "ms");
    show(n);
  });
}
function inView(n) {
  const r = n.getBoundingClientRect();
  return r.width + r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
}
function sweep(all) {
  document.querySelectorAll("#app .reveal:not(.in), #app [data-stagger] > :not(.in)").forEach((n) => { if (all || inView(n)) show(n); });
}
function wire() {
  if (wired) return;
  wired = true;
  window.addEventListener("scroll", () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(() => sweep(false), 250); }, { passive: true });
  window.addEventListener("resize", () => sweep(false));
  window.addEventListener("beforeprint", () => sweep(true));
  document.addEventListener("focusin", (ev) => {
    const t = ev.target;
    if (!t || !t.closest) return;
    const item = t.closest("[data-stagger-item], .reveal");
    if (item && !item.classList.contains("in")) show(item);
  });
}
export function beginReveal() {
  if (!("IntersectionObserver" in window)) { document.documentElement.classList.add("no-io"); return; }
  if (!io) io = new IntersectionObserver(onIntersect, { rootMargin: "0px 0px -6% 0px", threshold: 0 });
  wire();
  setTimeout(() => sweep(false), 1800);
}
// use:reveal — on .section, .stats and .reco roots: fade in when scrolled to.
export function reveal(node) {
  if (!io) return;
  node.classList.add("reveal");
  io.observe(node);
}
// use:stagger — on a container whose children should arrive one by one. Children added
// later (a tab switch, a filter, a runtime refresh) join the cascade through a MutationObserver.
export function stagger(node) {
  node.setAttribute("data-stagger", "");
  const add = (c) => {
    if (c.nodeType !== 1 || c.classList.contains("in")) return;
    c.setAttribute("data-stagger-item", "");
    if (io) io.observe(c); else c.classList.add("in");
  };
  Array.from(node.children).forEach(add);
  if (!("MutationObserver" in window)) return;
  const mo = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(add)));
  mo.observe(node, { childList: true });
  return { destroy() { mo.disconnect(); } };
}
// Sections inside a wrapper that was hidden (the evidence, a tab panel) never tripped the
// observer — reveal them directly; their items still cascade as they come into view.
export function revealAll(root) {
  if (!root) return;
  root.querySelectorAll(".reveal").forEach((n) => show(n));
}
