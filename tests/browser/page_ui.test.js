// Headless UI checks for the decision page — drives the REAL shipped template.html in
// Chrome (same harness pattern as per_setup_verdict.test.js: swap the inline JSON, load
// via file://, assert on the hydrated DOM). NOT part of the hermetic pytest suite — the
// deploy box has no Node — run on demand:  node tests/browser/page_ui.test.js
//
// Pins the UI-quality fixes: the hydrated DOM keeps an <h1>, an expanded issue row
// reveals the FULL untruncated title, and the hero setup-CTA action phrase can't wrap
// into orphaned fragments.

const path = require("path");
const fs = require("fs");
const puppeteer = require(process.env.PUPPETEER_PATH ||
  "/home/fede/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer");
const CHROME = process.env.CHROME_PATH ||
  "/home/fede/.cache/puppeteer/chrome/linux-149.0.7827.22/chrome-linux64/chrome";

const TEMPLATE = fs.readFileSync(path.join(__dirname, "..", "..", "web", "template.html"), "utf8");

function pageFor(data) {
  return TEMPLATE.replace(
    /(<script id="assessment-data" type="application\/json">)[\s\S]*?(<\/script>)/,
    (_, a, b) => a + "\n" + JSON.stringify(data) + "\n" + b);
}

const LONG_TITLE = "Intermittent memory_search \"index metadata is missing\" despite valid " +
  "builtin memory index; likely search/reindex race on all platforms with long tail";

const DATA = {
  schema_version: 6, assessed_at: "2026-06-07T00:00:00Z", version: "2026.6.1",
  recommendation: "⚠️", confidence: "medium", headline: "test headline", thesis: "t",
  freshness: { fresh: false },
  known_issues: [
    { number: 90361, title: LONG_TITLE, severity: "critical", category: "regression",
      affects_version: true, platforms: ["all"], components: ["memory"], reactions: 3 },
    { number: 2, title: "Second issue", severity: "high", category: "post_release",
      affects_version: false, platforms: ["linux"], components: ["gateway"] },
  ],
  evidence: {}, changes: {},
};

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  const tmp = path.join(require("os").tmpdir(), "page_ui_test.html");
  fs.writeFileSync(tmp, pageFor(DATA));
  await page.goto("file://" + tmp, { waitUntil: "networkidle0" });

  const checks = [];
  const t = (name, ok) => checks.push([name, ok]);

  // 1. The hydrated DOM keeps a top-level heading (render() wipes the SSR <h1>).
  const h1 = await page.evaluate(() => {
    const h = document.querySelector("h1.hero-version");
    return h ? h.textContent : null;
  });
  t("h1.hero-version exists after hydration", !!h1);
  t("h1 carries the version", (h1 || "").indexOf("2026.6.1") >= 0);
  t("exactly one h1", await page.evaluate(() => document.querySelectorAll("h1").length) === 1);

  // 2. Expanding an issue row reveals the FULL untruncated title (the row's .ititle
  //    is one-line ellipsized; the detail panel is where the whole title lives).
  const detTitle = await page.evaluate(() => {
    const row = document.querySelector(".issue .irow");
    row.click();
    const el = document.querySelector(".issue .idetail:not([hidden]) .idet-title");
    return el ? el.textContent : null;
  });
  t("expanded row shows the full title", detTitle === LONG_TITLE);

  // 3. The hero setup-CTA action phrase must not wrap into orphaned fragments.
  const ws = await page.evaluate(() =>
    getComputedStyle(document.querySelector(".setup-cta b")).whiteSpace);
  t("setup-CTA action phrase is nowrap", ws === "nowrap");

  t("no page errors", errs.length === 0);

  fs.unlinkSync(tmp);
  await browser.close();

  let failures = 0;
  for (const [name, ok] of checks) {
    if (!ok) failures++;
    console.log(`${ok ? "✓" : "✗"} ${name}`);
  }
  console.log(failures ? `\n${failures} FAILED` : `\nAll ${checks.length} page-ui checks passed`);
  process.exit(failures ? 1 : 0);
})();
