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
  "/home/user/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer");
const CHROME = process.env.CHROME_PATH ||
  "/home/user/.cache/puppeteer/chrome/linux-149.0.7827.22/chrome-linux64/chrome";

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
    { number: 3, title: "Third issue", severity: "medium", category: "regression",
      affects_version: false, platforms: ["linux"], components: ["gateway"] },
    { number: 4, title: "Fourth issue", severity: "low", category: "active",
      affects_version: false, platforms: ["discord"], components: ["gateway"] },
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

  // 4. Impact meters: continuous proportional fill — different issue volumes must
  //    read as different bar lengths (the old 5-segment quantizer saturated), and
  //    the grid stays sorted hot-first.
  const meters = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#components .plat")).map((c) => ({
      name: c.querySelector(".pname").textContent,
      w: parseInt(c.querySelector(".vfill").style.width, 10),
    })));
  const gw = meters.find((m) => /gateway/i.test(m.name));
  const mem = meters.find((m) => /memory/i.test(m.name));
  t("meter fills scale with volume (gateway 3 > memory 1)", !!gw && !!mem && gw.w > mem.w);
  t("busiest component fills the track", !!gw && gw.w === 100);
  t("grid is sorted hot-first", meters.length > 0 && /gateway/i.test(meters[0].name));
  t("old segmented meters are gone", await page.evaluate(() =>
    document.querySelectorAll(".plat .seg").length) === 0);
  t("platform heatmap carries all 11 surfaces (mobile/web/channels)", await page.evaluate(() => {
    const names = Array.from(document.querySelectorAll("#platforms .pname"))
      .map((n) => n.textContent);
    return document.querySelectorAll("#platforms .plat").length === 11 &&
      ["iOS", "Web UI", "WhatsApp", "Other channels"].every((x) => names.includes(x));
  }));

  // 5. Known-issues filters: category × subsystem are combinable dimensions.
  const visible = () => page.evaluate(() =>
    Array.from(document.querySelectorAll("#issues .issue"))
      .filter((r) => r.style.display !== "none")
      .map((r) => r.querySelector(".inum").textContent).join(","));
  await page.evaluate(() => document.querySelector('.ki-cats .ltab[data-f="regression"]').click());
  t("category filter alone", (await visible()) === "#90361,#3");
  await page.evaluate(() => document.querySelector('.ki-subs .fbtn[data-f="comp:gateway"]').click());
  t("category × subsystem combine", (await visible()) === "#3");
  await page.evaluate(() => document.querySelector('.ki-subs .fbtn[data-f="comp:gateway"]').click());
  t("re-clicking the subsystem chip clears that dimension", (await visible()) === "#90361,#3");

  // 6. A stack toggle rebuilds the issues section — BOTH filter dimensions survive.
  await page.evaluate(() => document.querySelector('.setup .pick[data-k="linux"]').click());
  const catPressed = await page.evaluate(() =>
    document.querySelector('.ki-cats .ltab[data-f="regression"]').getAttribute("aria-pressed"));
  t("category filter survives a stack toggle", catPressed === "true" && (await visible()) === "#90361,#3");

  // 7. "Clear all" wipes the stack and hides itself; the intro reads above the chips.
  const clearShown = await page.evaluate(() => !document.getElementById("stack-clear").hidden);
  await page.evaluate(() => document.getElementById("stack-clear").click());
  const cleared = await page.evaluate(() => ({
    pressed: document.querySelectorAll('.setup .pick[aria-pressed="true"]').length,
    hidden: document.getElementById("stack-clear").hidden,
  }));
  t("clear-all appears once a stack is picked", clearShown);
  t("clear-all wipes every pick and hides", cleared.pressed === 0 && cleared.hidden);
  t("setup intro sits above the chips", await page.evaluate(() => {
    const intro = document.querySelector(".setup .setup-intro");
    const chips = document.querySelector(".setup .chips");
    return !!intro && !!chips &&
      (intro.compareDocumentPosition(chips) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
  }));
  t("new surface chips are pickable (ios/android/web/whatsapp/other-channel)",
    await page.evaluate(() =>
      ["ios", "android", "web", "whatsapp", "other-channel"].every((k) =>
        !!document.querySelector('.setup .pick[data-k="' + k + '"]'))));

  // 8. A11y: skip-link + live region present; changelog tabs carry a roving tabindex.
  t("skip-link targets #app", await page.evaluate(() => {
    const a = document.querySelector("a.skip-link");
    return !!a && a.getAttribute("href") === "#app" && !!document.getElementById("app");
  }));
  t("polite live region exists", await page.evaluate(() => {
    const r = document.getElementById("live");
    return !!r && r.getAttribute("aria-live") === "polite";
  }));
  t("changelog tabs have a roving tabindex", await page.evaluate(() => {
    const ts = Array.from(document.querySelectorAll('.tabs[role="tablist"] .tab'));
    return ts.length >= 2 &&
      ts.filter((x) => x.tabIndex === 0).length === 1 &&
      ts.filter((x) => x.tabIndex === -1).length === ts.length - 1;
  }));

  // 9. A real (trusted) filter click announces the result count to the live region.
  await page.click('.ki-cats .ltab[data-f="all"]');
  await new Promise((r) => setTimeout(r, 250));
  const liveMsg = await page.evaluate(() => document.getElementById("live").textContent);
  t("filter click announces to the live region", /4 of 4 issues shown/.test(liveMsg));

  // 10. The ⚖︎ review chip expands into the validator's actual findings.
  const revState = await page.evaluate(() => {
    const btn = document.querySelector('.conf-row .chip[aria-controls="rev-detail"]');
    const panel = document.getElementById("rev-detail");
    if (!btn || !panel) return null;
    const hiddenBefore = panel.hidden;
    btn.click();
    return { isButton: btn.tagName === "BUTTON", hiddenBefore, hiddenAfter: panel.hidden,
             text: panel.textContent };
  });
  t("review chip is an expander button, panel hidden by default",
    !!revState && revState.isButton && revState.hiddenBefore === true);
  t("expanding reveals the validator's words",
    !!revState && revState.hiddenAfter === false &&
    revState.text.indexOf("checked the labels, sound") >= 0 &&
    revState.text.indexOf("#777") >= 0);

  // 11b. Per-component verdict line: chips per affected component, hot-first; the
  //      fixture's cross-cutting "all" critical pins every component to the global ⚠️.
  t("verdict-by-component line renders, hot-first, pinned to global", await page.evaluate(() => {
    const vl = document.getElementById("verdict-line");
    if (!vl || !vl.closest("#ltp-impact")) return false;
    const chips = Array.from(vl.querySelectorAll(".vchip"));
    const note = vl.querySelector(".vline-note");
    return chips.length === 2 &&
      /gateway/i.test(chips[0].textContent) && chips[0].querySelector(".vc-n").textContent === "3" &&
      chips.every((c) => c.querySelector(".vc-em").textContent === "⚠️") &&   // no softening past a cross-cutting blocker
      chips.every((c) => c.classList.contains("hot")) &&
      !!note && note.textContent.indexOf("9 other components") >= 0;
  }));

  // 11a. Verdict track record lives in the History tab: per-version rows with
  //      path + direction badges, and the summary counts repeat-assessed versions.
  t("track record renders rows with direction badges", await page.evaluate(() => {
    const sec = document.getElementById("track-record");
    if (!sec || !sec.closest("#ltp-history")) return false;   // must sit in the History panel
    const rows = sec.querySelectorAll(".tr-row");
    const badges = Array.from(sec.querySelectorAll(".tr-badge")).map((b) => b.textContent);
    return rows.length === 2 && badges.includes("✓ held") && badges.includes("↓ hardened") &&
      sec.querySelector(".tr-sum").textContent.indexOf("2 versions") >= 0 &&
      sec.querySelector(".tr-row.cur .tr-ver").textContent.indexOf("2026.6.1") >= 0;
  }));

  // 11. Flip-conditions section renders with the issue reference linkified.
  t("flip-conditions section renders and links the cited issue", await page.evaluate(() => {
    const sec = document.getElementById("flip");
    if (!sec) return false;
    const link = sec.querySelector('a[href*="issues/90361"]');
    return sec.textContent.indexOf("hardens to ⏸️") >= 0 && !!link;
  }));

  // 12. Report-a-problem: footer + about carry a prefilled new-issue link that
  //     lands with this page's version and verdict already in the title.
  t("report-a-problem links are prefilled with page state", await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll('a[href*="openclaw_status_app/issues/new"]'));
    if (links.length < 2) return false;                       // footer + about
    const href = decodeURIComponent(links[0].getAttribute("href"));
    return href.indexOf("v2026.6.1") >= 0 && href.indexOf("⚠️") >= 0 &&
      href.indexOf("What looks wrong?") >= 0;
  }));

  // 13. UI-revamp guards: the long-tail tab strip is a real tablist with a roving
  //     tabindex + exactly one selected tab, and the meter cards carry the inline
  //     SVG icons (the emoji glyphs were replaced by icon()) with labels intact.
  t("long-tail tablist has roving tabindex and one selected tab", await page.evaluate(() => {
    const ts = Array.from(document.querySelectorAll('.ltabs[role="tablist"] .ltab'));
    return ts.length >= 2 &&
      ts.filter((x) => x.tabIndex === 0).length === 1 &&
      ts.filter((x) => x.tabIndex === -1).length === ts.length - 1 &&
      ts.filter((x) => x.getAttribute("aria-selected") === "true").length === 1;
  }));
  t("meter cards carry inline svg icons with names intact", await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll("#components .plat"));
    return cards.length > 0 &&
      cards.every((c) => !!c.querySelector("svg.ic-svg") && !!c.querySelector(".pname").textContent.trim());
  }));

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
