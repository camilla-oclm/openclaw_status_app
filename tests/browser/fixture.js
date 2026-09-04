// Shared by the browser suites: the puppeteer / Chromium discovery, the real template,
// and one realistic payload. Not a test itself.
//
// CI sets PUPPETEER_PATH/CHROME_PATH; local runs discover the toolchain so no
// machine path is hardcoded. Two shapes are supported: an npx-cached `puppeteer`
// with its own downloaded Chrome, and a repo-local `puppeteer-core` driving a
// system Chromium (the usual arrangement on a Linux box that already has one).
const path = require("path");
const fs = require("fs");
const os = require("os");

function firstUnder(base, sub) {
  try {
    for (const d of fs.readdirSync(base)) {
      const p = path.join(base, d, sub);
      if (fs.existsSync(p)) return p;
    }
  } catch {}
  return null;
}
function firstExisting(candidates) {
  return candidates.find((p) => p && fs.existsSync(p)) || null;
}
const REPO_ROOT = path.join(__dirname, "..", "..");
const puppeteer = require(process.env.PUPPETEER_PATH ||
  firstUnder(path.join(os.homedir(), ".npm", "_npx"), path.join("node_modules", "puppeteer")) ||
  firstExisting([path.join(REPO_ROOT, "node_modules", "puppeteer"),
                 path.join(REPO_ROOT, "node_modules", "puppeteer-core")]) ||
  "puppeteer");
// puppeteer-core ships no browser, so a system Chromium is the required partner.
const CHROME = process.env.CHROME_PATH ||
  firstUnder(path.join(os.homedir(), ".cache", "puppeteer", "chrome"),
             path.join("chrome-linux64", "chrome")) ||
  firstExisting(["/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]);

const TEMPLATE = fs.readFileSync(path.join(REPO_ROOT, "web", "template.html"), "utf8");

// The real shipped template with the payload swapped in — the same regex render.py uses.
function pageFor(data) {
  return TEMPLATE.replace(
    /(<script id="assessment-data" type="application\/json">)[\s\S]*?(<\/script>)/,
    (_, a, b) => a + "\n" + JSON.stringify(data) + "\n" + b);
}

const LONG_TITLE = "Intermittent memory_search \"index metadata is missing\" despite valid " +
  "builtin memory index; likely search/reindex race on all platforms with long tail";

const DATA = {
  schema_version: 1, assessed_at: "2026-06-07T00:00:00Z", version: "2026.6.1",   // matches render.SCHEMA_VERSION
  recommendation: "⚠️", confidence: "medium", headline: "test headline", thesis: "t",
  freshness: { fresh: false },
  // known_issues carry the real-shape keys (weight / version_match / tag_source) the emitted
  // payload has (D26). Weights descend with severity so display order is unchanged.
  known_issues: [
    { number: 90361, title: LONG_TITLE, severity: "critical", category: "regression",
      affects_version: true, platforms: ["all"], components: ["memory"], reactions: 3,
      weight: 90, version_match: "exact", tag_source: "derived" },
    { number: 2, title: "Second issue", severity: "high", category: "post_release",
      affects_version: false, platforms: ["linux"], components: ["gateway"],
      weight: 62, version_match: "none", tag_source: "derived" },
    { number: 3, title: "Third issue", severity: "medium", category: "regression",
      affects_version: false, platforms: ["linux"], components: ["gateway"],
      weight: 40, version_match: "none", tag_source: "derived" },
    { number: 4, title: "Fourth issue", severity: "low", category: "active",
      affects_version: false, platforms: ["discord"], components: ["gateway"],
      weight: 20, version_match: "none", tag_source: "derived" },
  ],
  evidence: {},
  changes: { features: [{ title: "New turbo mode", value: "twice the speed" }],
             fixes: [{ title: "Fixed the flux capacitor", verified: true }], breaking: [] },
  flip_conditions: ["⚠️ hardens to ⏸️ if #90361 is confirmed on stable"],
  track_record: {
    versions: [
      { version: "2026.6.1", runs: 3, first: { t: "2026-06-05", rec: "⚠️" },
        last: { t: "2026-06-07", rec: "⚠️" }, path: ["⚠️"], direction: "held", current: true },
      { version: "2026.5.9", runs: 4, first: { t: "2026-06-01", rec: "⚠️" },
        last: { t: "2026-06-04", rec: "⏸️" }, path: ["⚠️", "⏸️"], direction: "hardened", current: false },
    ],
    summary: { tracked: 2, held: 1, hardened: 1, softened: 0, mixed: 0, single: 0 },
  },
  review: { validated: true, unreviewed: false, agreed: true, refined: false,
            primary_recommendation: "⚠️", critique: "checked the labels, sound",
            detail: { critique: "checked the labels, sound", suggested_recommendation: "",
                      miscategorized_issues: [], missed_issues: ["#777 memory race"],
                      logical_errors: [], overruled_claims: [] } },
  // The deterministic evidence gate (verdict.py) + the best version to run today.
  evidence_gate: { verdict: "⚠️", blockers: [90361], widespread: [], serious: [], blocker_count: 1,
                   reason: "1 credible blocking issue confirmed for this version, none widespread.",
                   floor_applied: false, departure: { departed: false, reason: "" } },
  recommended_version: { version: "2026.5.9", kind: "settled", published_at: "2026-05-20", age_days: 18,
                         recommendation: "⚠️", gate: "⚠️", blocker_count: 1, min_days: 7, considered: 2,
                         blockers: [{ number: 777, title: "memory race", severity: "high" }],
                         latest: { version: "2026.6.1", age_days: 2, recommendation: "⚠️", settled: false } },
};

module.exports = { puppeteer, CHROME, REPO_ROOT, TEMPLATE, pageFor, LONG_TITLE, DATA };
