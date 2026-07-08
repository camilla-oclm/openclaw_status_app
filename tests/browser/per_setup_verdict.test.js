// Headless matrix test for the per-setup ("Your setup") verdict — audit M8.
//
// Drives the REAL shipped setupVerdict()/setupBlockers() from web/template.html via the
// guarded window.__perSetupTest hook (no Python re-implementation that could drift from the
// client-only JS). NOT part of the hermetic pytest suite — the deploy box has no Node — so
// run it on demand where puppeteer + Chrome exist:  node tests/browser/per_setup_verdict.test.js
//
// Verifies the conservative invariants: softens by at most one notch, never harsher than the
// global verdict, never ⏸️→✅, fresh never softens, no-stack never softens, and a version-
// confirmed high/critical blocker that hits the stack (or is cross-cutting "all") blocks softening.

const path = require("path");
const fs = require("fs");
const puppeteer = require(process.env.PUPPETEER_PATH ||
  "/home/user/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer");
const CHROME = process.env.CHROME_PATH ||
  "/home/user/.cache/puppeteer/chrome/linux-149.0.7827.22/chrome-linux64/chrome";

const TEMPLATE = fs.readFileSync(path.join(__dirname, "..", "..", "web", "template.html"), "utf8");
const ORDER = ["✅", "⚠️", "⏸️"];
const ci = (r) => ORDER.indexOf(r);

function pageFor(data) {
  // Replace the inline assessment-data JSON with the case's DATA.
  return TEMPLATE.replace(
    /(<script id="assessment-data" type="application\/json">)[\s\S]*?(<\/script>)/,
    (_, a, b) => a + "\n" + JSON.stringify(data) + "\n" + b);
}

const issue = (o) => Object.assign(
  { number: 1, title: "x", severity: "low", affects_version: false, platforms: [], components: [],
    weight: 0, version_match: "none", tag_source: "derived" }, o);   // real-shape keys (D26)

// version, freshness, known_issues for the page DATA
const D = (rec, fresh, issues) => ({
  schema_version: 1, assessed_at: "2026-06-07T00:00:00Z", version: "2026.6.1",   // matches render.SCHEMA_VERSION
  recommendation: rec, confidence: "high", headline: "t", thesis: "t",
  freshness: { fresh: !!fresh }, known_issues: issues || [], evidence: {}, changes: {},
});

// [name, DATA, stack, expectedRec]
const CASES = [
  ["⏸️ softens one notch for an unaffected stack",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["macos"] })]),
   ["linux"], "⚠️"],
  ["⏸️ blocked by a version-confirmed critical ON the stack",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["linux"] })]),
   ["linux"], "⏸️"],
  ["⏸️ blocked by a cross-cutting 'all' critical",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["all"] })]),
   ["windows"], "⏸️"],
  ["a NON-version-confirmed critical does NOT block softening",
   D("⏸️", false, [issue({ severity: "critical", affects_version: false, platforms: ["linux"] })]),
   ["linux"], "⚠️"],
  ["fresh release never softens",
   D("⏸️", true, []), ["linux"], "⏸️"],
  ["no stack picked never softens",
   D("⏸️", false, []), [], "⏸️"],
  ["⚠️ softens to ✅ when clear",
   D("⚠️", false, []), ["discord"], "✅"],
  ["✅ cannot soften below ✅",
   D("✅", false, []), ["linux"], "✅"],
  ["component-only stack hit blocks softening",
   D("⏸️", false, [issue({ severity: "high", affects_version: true, components: ["auth"] })]),
   ["auth"], "⏸️"],
  ["⏸️ blocked by a version-confirmed iOS critical ON an iOS stack",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["ios"] })]),
   ["ios"], "⏸️"],
  ["a mobile-only blocker spares a desktop stack",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["ios"] })]),
   ["linux"], "⚠️"],
  ["a long-tail channel blocker pins the other-channel stack",
   D("⏸️", false, [issue({ severity: "high", affects_version: true, platforms: ["other-channel"] })]),
   ["other-channel"], "⏸️"],
  ["a long-tail channel blocker spares a discord stack",
   D("⏸️", false, [issue({ severity: "high", affects_version: true, platforms: ["other-channel"] })]),
   ["discord"], "⚠️"],
  // D03 client fail-closed: a version-confirmed blocker we could not classify to ANY platform
  // (platforms=[]) must NOT spare a platform-only picker. Without the fallback the old client
  // softened ⏸️→⚠️ here; with it the picker is pinned. (The server now ships such a blocker as
  // 'all', but the client stays fail-closed in its own right.)
  ["⏸️ NOT softened by a version-confirmed blocker with no platform, on a platform-only stack",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: [], components: ["auth"] })]),
   ["windows"], "⏸️"],
  // ...but a component-only picker still pins via the component axis (fallback is platform-only).
  ["⏸️ that same no-platform blocker pins a picker who selected its component",
   D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: [], components: ["auth"] })]),
   ["auth"], "⏸️"],
];

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  let failures = 0;
  for (const [name, data, stack, expected] of CASES) {
    const page = await browser.newPage();
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    const tmp = path.join(require("os").tmpdir(), "psv_" + Math.abs(hash(name)) + ".html");
    fs.writeFileSync(tmp, pageFor(data));
    await page.goto("file://" + tmp, { waitUntil: "networkidle0" });
    const svd = await page.evaluate((ks) => {
      window.__perSetupTest.setStack(ks);
      return window.__perSetupTest.setupVerdict();
    }, stack);
    fs.unlinkSync(tmp);
    await page.close();

    const checks = [];
    checks.push(["rec is valid", ci(svd.rec) >= 0]);
    checks.push(["expected " + expected, svd.rec === expected]);
    checks.push(["never harsher than global", ci(svd.rec) <= ci(svd.global)]);
    checks.push(["at most one notch", ci(svd.global) - ci(svd.rec) <= 1]);
    checks.push(["never ⏸️→✅", !(svd.global === "⏸️" && svd.rec === "✅")]);
    checks.push(["no page errors", errs.length === 0]);

    const bad = checks.filter(([, ok]) => !ok);
    if (bad.length) {
      failures++;
      console.log(`✗ ${name}  (got ${svd.rec}, global ${svd.global})`);
      bad.forEach(([d]) => console.log(`    - ${d}`));
    } else {
      console.log(`✓ ${name}  → ${svd.rec}`);
    }
  }
  // ── keyVerdict (the per-component verdict line's core) — same conservative rules,
  //    evaluated per key set without a picked stack ─────────────────────────────────
  // [name, DATA, plats, comps, expectedRec]
  const KV_CASES = [
    ["component clear of blockers softens one notch",
     D("⏸️", false, [issue({ severity: "high", affects_version: true, components: ["memory"] })]),
     [], ["gateway"], "⚠️"],
    ["component carrying the blocker keeps the global verdict",
     D("⏸️", false, [issue({ severity: "high", affects_version: true, components: ["memory"] })]),
     [], ["memory"], "⏸️"],
    ["cross-cutting 'all' blocker pins EVERY component to global",
     D("⏸️", false, [issue({ severity: "critical", affects_version: true, platforms: ["all"], components: ["build"] })]),
     [], ["gateway"], "⏸️"],
    ["fresh release never softens a component",
     D("⏸️", true, []), [], ["gateway"], "⏸️"],
    ["global ✅ can't soften below ✅",
     D("✅", false, []), [], ["gateway"], "✅"],
  ];
  let kvTotal = 0;
  for (const [name, data, plats, comps, expected] of KV_CASES) {
    kvTotal++;
    const page = await browser.newPage();
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    const tmp = path.join(require("os").tmpdir(), "psv_kv_" + Math.abs(hash(name)) + ".html");
    fs.writeFileSync(tmp, pageFor(data));
    await page.goto("file://" + tmp, { waitUntil: "networkidle0" });
    const kv = await page.evaluate(
      (p, c) => window.__perSetupTest.keyVerdict(p, c), plats, comps);
    fs.unlinkSync(tmp);
    await page.close();
    const ok = kv.rec === expected && ci(kv.rec) <= ci(kv.global) &&
      (ci(kv.global) - ci(kv.rec) <= 1) && errs.length === 0;
    if (!ok) { failures++; console.log(`✗ ${name}  (got ${kv.rec}, global ${kv.global})`); }
    else console.log(`✓ ${name}  → ${kv.rec}`);
  }

  await browser.close();
  console.log(failures ? `\n${failures} FAILED`
    : `\nAll ${CASES.length + kvTotal} per-setup cases passed`);
  process.exit(failures ? 1 : 0);
})();

function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; } return h; }
