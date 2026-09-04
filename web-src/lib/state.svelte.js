// The page's reactive state: the assessment payload and the visitor's picked "stack".
//
// DATA comes from the inlined assessment-data JSON block (the build-time copy, always
// present) and, when reachable, a runtime fetch('latest.json') — see main.js. It is held
// raw (replaced wholesale, never mutated) so a 60-issue payload isn't deep-proxied.
//
// STACK is a { key: true } set. A `?stack=linux,gateway` URL param wins over localStorage
// at boot (shared / bookmarked links open pre-picked; unknown keys are dropped) but is NOT
// saved until the visitor's first own toggle — opening someone's link doesn't overwrite
// this device's remembered stack. The param is a QUERY param, not the hash: in-page anchors
// (skip-link, permalinks) keep working untouched.
import { PLAT_LABEL, COMP_LABEL } from "./tables.js";

let data = $state.raw(null);
const stack = $state({});

export const app = {
  get data() { return data; },
  set data(v) { data = v; },
  get stack() { return stack; },
};

export function bootStack() {
  try {
    const fromUrl = (new URLSearchParams(location.search).get("stack") || "")
      .split(",").map((s) => s.trim().toLowerCase())
      .filter((k) => PLAT_LABEL[k] || COMP_LABEL[k]);
    const saved = fromUrl.length ? fromUrl : (JSON.parse(localStorage.getItem("oc-stack")) || []);
    for (const k of saved) stack[k] = true;
  } catch (e) {}
}
export function stackKeys() { return Object.keys(stack).filter((k) => stack[k]); }
export function stackActive() { return stackKeys().length > 0; }
// The stack mixes platform keys and component keys (disjoint sets); split by membership.
export function stackPlatforms() { return stackKeys().filter((k) => PLAT_LABEL[k]); }
export function stackComponents() { return stackKeys().filter((k) => COMP_LABEL[k]); }

export function saveStack() {
  try { localStorage.setItem("oc-stack", JSON.stringify(stackKeys())); } catch (e) {}
  // Mirror the stack into the URL so the address bar is always a shareable deep link
  // (replaceState: no history spam, hash/anchors preserved).
  try {
    const qs = new URLSearchParams(location.search), ks = stackKeys();
    if (ks.length) qs.set("stack", ks.join(",")); else qs.delete("stack");
    const q = qs.toString().replace(/%2C/gi, ",");   // commas are query-legal; keep the link readable
    window.history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + location.hash);
  } catch (e) {}
}
export function toggleKey(k) {
  if (stack[k]) delete stack[k]; else stack[k] = true;
  saveStack();
}
export function clearStack() {
  for (const k of Object.keys(stack)) delete stack[k];
  saveStack();
}
// The share link, built from the CURRENT stack the way saveStack mirrors it — never from
// location.href, which lacks ?stack= until a toggle has run (a boot-restored stack never
// wrote the URL). Constructed WITHOUT touching localStorage or the URL. (D17)
export function shareLink() {
  try {
    const qs = new URLSearchParams(location.search), ks = stackKeys();
    if (ks.length) qs.set("stack", ks.join(",")); else qs.delete("stack");
    const q = qs.toString().replace(/%2C/gi, ",");
    return location.origin + location.pathname + (q ? "?" + q : "") + location.hash;
  } catch (e) { return location.href; }
}
// Test hook helper (tests/browser/per_setup_verdict.test.js): replace the stack outright.
export function setStack(keys) {
  for (const k of Object.keys(stack)) delete stack[k];
  (keys || []).forEach((k) => { stack[k] = true; });
}
