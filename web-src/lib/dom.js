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
// Sections fade in on their first visible pixel (threshold:0 — a fractional threshold is
// unreachable for very tall sections on a phone). A safety net reveals anything still
// hidden 1.8 s after render, so nothing is ever gated on JS animation. Only the render
// pass observes: a section (re)mounted later shows immediately, as before.
let io = null, booting = false, bootTimer = 0;
export function beginReveal() {
  booting = true;
  if (!("IntersectionObserver" in window)) { io = null; return; }
  if (!io) {
    io = new IntersectionObserver((es) => {
      es.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px 10% 0px", threshold: 0 });
  }
  clearTimeout(bootTimer);
  bootTimer = setTimeout(() => {
    booting = false;
    document.querySelectorAll("#app .reveal:not(.in)").forEach((n) => { if (io) io.unobserve(n); n.classList.add("in"); });
  }, 1800);
}
// use:reveal — on .section, .stats and .reco roots.
export function reveal(node) {
  if (!booting) return;
  if (!io) { node.classList.add("in"); return; }
  node.classList.add("reveal");
  io.observe(node);
}
// Sections inside a wrapper that was hidden never tripped the observer — reveal directly.
export function revealAll(root) {
  if (!root) return;
  root.querySelectorAll(".reveal").forEach((n) => n.classList.add("in"));
}
