// Headless UI checks for the decision page — drives the REAL shipped template.html in
// Chrome (same harness pattern as per_setup_verdict.test.js: swap the inline JSON, load
// via file://, assert on the hydrated DOM). NOT part of the hermetic pytest suite — the
// deploy box has no Node — run on demand:  node tests/browser/page_ui.test.js
//
// Pins the UI-quality fixes: the hydrated DOM keeps an <h1>, an expanded issue row
// reveals the FULL untruncated title, and the answer-first layout leads with a plain
// status word + per-platform strip while the evidence sits behind one toggle.

const path = require("path");
const fs = require("fs");
const os = require("os");

// The toolchain discovery, the real template and the payload live in fixture.js (shared
// with the contrast audit). CI sets PUPPETEER_PATH/CHROME_PATH; local runs discover them.
const { puppeteer, CHROME, pageFor, LONG_TITLE, DATA } = require("./fixture.js");

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

  // 0. Answer-first layout: the status word + one-liner lead, every platform chip carries its
  //    own verdict glyph + tone (the fixture's cross-cutting critical pins them all to ⚠️),
  //    the best-version card names a version + a pinned install command, the WHY card counts
  //    the gate's credible blockers, and the evidence is collapsed until asked for.
  const lead = await page.evaluate(() => ({
    word: (document.querySelector(".hero .verdict .answer-word") || {}).textContent,
    line: (document.querySelector(".hero .answer-line") || {}).textContent || "",
    tone: document.body.getAttribute("data-tone"),
    strip: Array.from(document.querySelectorAll(".setup .chips:not(.comp-chips) .pick[data-k]"))
      .map((b) => ({ k: b.getAttribute("data-k"), pv: (b.querySelector(".pv") || {}).textContent, cls: b.className })),
    note: (document.getElementById("strip-note") || {}).textContent,
    compHidden: (document.getElementById("comp-chips") || {}).hidden,
    reco: (document.querySelector("#best-version .reco-v") || {}).textContent,
    recoCmd: (document.querySelector("#best-version .reco-cmd") || {}).textContent,
    recoWhy: (document.querySelector("#best-version .reco-why") || {}).textContent || "",
    recoChips: (document.querySelector("#best-version .reco-chips") || {}).textContent || "",
    why: (document.querySelector("#why .why-bad h3") || {}).textContent,
    whyLink: !!document.querySelector('#why .why-bad a[href*="issues/90361"]'),
    gateChip: Array.from(document.querySelectorAll(".conf-row .chip")).some((c) => /Evidence gate/.test(c.textContent)),
    detailsHidden: (document.getElementById("details-body") || {}).hidden,
    toggle: (document.querySelector(".details-toggle") || { getAttribute: () => null }).getAttribute("aria-expanded"),
    heroOrder: (() => {   // hero → best version → why → flip → details → footer
      const ids = Array.from(document.querySelectorAll("#app > *")).map((n) => n.id || n.className.split(" ")[0]);
      return ids.join(",");
    })(),
  }));
  t("status word leads the hero", lead.word === "Update with care" && lead.tone === "warn");
  t("one-liner says the blockers can hit any setup", /can hit any setup/.test(lead.line));
  const platKeys = ["windows", "macos", "linux", "ios", "android", "web", "discord", "slack", "telegram", "whatsapp", "other-channel"];
  t("platform strip: 11 chips, each with its own verdict glyph + tone",
    lead.strip.length === 11 && platKeys.every((k) => lead.strip.some((c) => c.k === k)) &&
    lead.strip.every((c) => c.pv === "⚠️" && /pv-warn/.test(c.cls)));
  t("strip note: every platform is affected (cross-cutting critical)", lead.note === "every platform is affected");
  t("component chips are tucked behind the toggle by default", lead.compHidden === true);
  t("best-version card names the settled release + a pinned install command",
    lead.reco === "v2026.5.9" && lead.recoCmd === "npm install -g openclaw@2026.5.9" &&
    /18 days in the field with no widespread breaker/.test(lead.recoWhy) &&
    /The latest, v2026.6.1, came out 2 days ago/.test(lead.recoWhy) &&
    /1 credible, none widespread/.test(lead.recoChips));
  t("WHY card counts the gate's credible blockers and links them",
    lead.why === "What's broken · 1 credible blocking issue" && lead.whyLink);
  t("evidence-gate chip shows in the hero", lead.gateChip);
  t("full evidence is collapsed by default", lead.detailsHidden === true && lead.toggle === "false");
  t("section order: hero, best version, why, flip, details, footer",
    lead.heroOrder === "hero,best-version,why,flip,details,foot");

  // The redesign collapses the evidence behind one toggle; open it so the checks below can
  // interact with the sections it holds (a visibility-dependent page.click needs this).
  const openEvidence = () => page.evaluate(() => {
    const b = document.querySelector(".details-toggle");
    if (b && b.getAttribute("aria-expanded") !== "true") b.click();
  });
  await openEvidence();
  t("toggle opens the evidence", await page.evaluate(() =>
    document.getElementById("details-body").hidden === false &&
    document.querySelector(".details-toggle").getAttribute("aria-expanded") === "true" &&
    /Hide the full evidence/.test(document.querySelector(".details-toggle .dt-main").textContent)));

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

  // 3. Picking a platform chip highlights it (ring) without losing its verdict tone.
  await page.evaluate(() => document.querySelector('.setup .pick[data-k="linux"]').click());
  const picked = await page.evaluate(() => {
    const b = document.querySelector('.setup .pick[data-k="linux"]');
    return { pressed: b.getAttribute("aria-pressed"), cls: b.className,
             risk: !!document.querySelector(".setup .risk .sv") };
  });
  t("picked chip is pressed, keeps its verdict tone, and the per-setup panel appears",
    picked.pressed === "true" && /pv-warn/.test(picked.cls) && picked.risk);
  await page.evaluate(() => document.querySelector('.setup .pick[data-k="linux"]').click());   // un-pick

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
  t("setup strip lives inside the hero", await page.evaluate(() =>
    !!document.querySelector(".hero #setup")));
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

  // 14. Stack-in-URL: a ?stack= link boots pre-picked (unknown keys dropped, the
  //     device's saved stack untouched), toggles keep the address shareable, and
  //     the copy-link chip announces either way (clipboard success or fallback).
  //     Served over loopback HTTP — file:// documents have origin "null", where
  //     Chrome rejects history.replaceState with a URL (production is HTTPS).
  const http = require("http");
  const server = http.createServer((req, res) => {
    if ((req.url || "").split("?")[0] !== "/") { res.statusCode = 404; return res.end(); }
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end(pageFor(DATA));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = "http://127.0.0.1:" + server.address().port;
  await page.goto(base + "/?stack=linux,gateway,bogus", { waitUntil: "networkidle0" });
  const urlBoot = await page.evaluate(() => ({
    pressed: Array.from(document.querySelectorAll('.setup .pick[aria-pressed="true"]'))
      .map((b) => b.getAttribute("data-k")).sort().join(","),
    stored: localStorage.getItem("oc-stack"),
    shareShown: !document.getElementById("stack-share").hidden,
  }));
  t("?stack= URL boots pre-picked, unknown keys dropped", urlBoot.pressed === "gateway,linux");
  t("a shared link never overwrites the device's saved stack", urlBoot.stored === null);
  t("share chip shows for a URL-booted stack", urlBoot.shareShown);
  await page.evaluate(() => document.querySelector('.setup .pick[data-k="windows"]').click());
  const search = await page.evaluate(() => location.search);
  t("toggling rewrites the shareable URL (readable commas)",
    /^\?stack=/.test(search) && search.includes("windows") &&
    search.includes("linux") && !search.includes("%2C"));
  await page.click("#stack-share");
  await new Promise((r) => setTimeout(r, 300));
  t("copy-link chip announces", /cop(y|ied)/i.test(
    await page.evaluate(() => document.getElementById("live").textContent)));

  // D17: a stack RESTORED FROM localStorage at boot (no ?stack in the URL, no toggle yet) must
  // still yield a share link carrying ?stack — the chip previously copied location.href, which
  // had no ?stack, while announcing "pre-picked".
  await page.evaluate(() => localStorage.setItem("oc-stack", JSON.stringify(["macos"])));
  await page.goto(base + "/", { waitUntil: "networkidle0" });   // bare URL; stack comes from localStorage
  const restored = await page.evaluate(() => ({
    search: location.search,
    pressed: Array.from(document.querySelectorAll('.setup .pick[aria-pressed="true"]'))
      .map((b) => b.getAttribute("data-k")).join(","),
  }));
  const copied = await page.evaluate(() => new Promise((resolve) => {
    if (!navigator.clipboard) navigator.clipboard = {};
    navigator.clipboard.writeText = (s) => { resolve(s); return Promise.resolve(); };
    document.getElementById("stack-share").click();
  }));
  t("boot-restored stack: the share chip copies a link WITH ?stack (D17)",
    restored.search === "" && restored.pressed === "macos" && /[?&]stack=[^&]*macos/.test(copied));

  // issues_capped: at the ledger cap the count surfaces read "N+" (metric tile + section
  // head) so a saturated window doesn't read as "nothing new" — while the literal list
  // counts (the "All (N)" filter tab) stay numeric, since they count the items shown.
  DATA.issues_capped = true;
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  const capped = await page.evaluate(() => ({
    tile: Array.from(document.querySelectorAll(".stat"))
      .map((s) => ({ v: s.querySelector(".v").textContent, l: s.querySelector(".l").textContent }))
      .find((s) => s.l === "Known issues"),
    secCount: (document.querySelector("#issues .sec-count") || {}).textContent,
    allTab: (document.querySelector('#issues .ltab[data-f="all"]') || {}).textContent,
  }));
  t("capped issue count shows N+ in tile and section head; list tab stays literal",
    !!capped.tile && capped.tile.v === "4+" && capped.secCount === "4+" &&
    /All \(4\)/.test(capped.allTab || ""));
  delete DATA.issues_capped;

  // Calibration note: prevalence + upstream-velocity line under the issues head when the
  // payload carries calibration; absent payload → no node at all (old assessments).
  DATA.calibration = { npm_weekly_downloads: 1956582, tracked_total: 91, closed_completed: 31 };
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  const calNote = await page.evaluate(
    () => (document.querySelector("#issues .cal-note") || {}).textContent || "");
  t("calibration note shows install base + upstream fix velocity",
    calNote.includes("~2.0M weekly npm installs") &&
    calNote.includes("31 of 91 issues ever tracked for this release already fixed upstream"));
  delete DATA.calibration;
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  const noCal = await page.evaluate(() => document.querySelector("#issues .cal-note"));
  t("calibration note absent when the payload has no calibration", noCal === null);

  // Trends cap annotation: an issue-count series pinned at the ledger cap is SATURATION
  // (the headline counters say "60+") — the pressure + severity charts must mark it with
  // the dashed tracking-cap line, and the pressure legend must read "60+ at cap".
  DATA.issues_cap = 60;
  DATA.timeline = [
    { t: "2026-06-05T00:00:00Z", version: "2026.6.1", recommendation: "⚠️",
      issues: 60, regressions: 3, critical: 12, high: 48, medium: 0, low: 0 },
    { t: "2026-06-06T00:00:00Z", version: "2026.6.1", recommendation: "⚠️",
      issues: 60, regressions: 4, critical: 12, high: 48, medium: 0, low: 0 },
    { t: "2026-06-07T00:00:00Z", version: "2026.6.1", recommendation: "⚠️",
      issues: 60, regressions: 4, critical: 12, high: 48, medium: 0, low: 0 },
  ];
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  const trendCap = await page.evaluate(() => ({
    caps: Array.from(document.querySelectorAll("#trends .tcap")).map((x) => x.textContent),
    legend: (document.querySelector("#trends .tcard .tc-legend") || {}).textContent || "",
  }));
  t("capped timeline: tracking-cap line in pressure+severity charts, legend reads 60+ at cap",
    trendCap.caps.length === 2 && trendCap.caps.every((s) => s === "tracking cap (60)") &&
    trendCap.legend.includes("60+ at cap"));
  DATA.timeline = DATA.timeline.map((r, i) => ({ ...r, issues: 20 + i, critical: 2, high: 18 + i }));
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  const belowCap = await page.evaluate(() => ({
    caps: document.querySelectorAll("#trends .tcap").length,
    legend: (document.querySelector("#trends .tcard .tc-legend") || {}).textContent || "",
  }));
  t("below-cap timeline: no cap annotation anywhere",
    belowCap.caps === 0 && !belowCap.legend.includes("at cap"));
  delete DATA.timeline; delete DATA.issues_cap;

  // Stacked hotfix chain (v2026.7.1-1 + -2, 2026-08-04): the Fixes tile's scope line
  // must widen to the chain, and revert to "in this release" without one.
  const fixesTileSub = () => page.evaluate(() => {
    const tile = Array.from(document.querySelectorAll(".stat"))
      .find((s) => s.querySelector(".l").textContent === "Fixes shipped");
    return tile ? tile.querySelector(".s").textContent : "";
  });
  DATA.hotfix_chain = ["2026.7.1-1", "2026.7.1-2"];
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  t("hotfix chain: Fixes tile scoped 'across 2 stacked hotfixes'",
    (await fixesTileSub()) === "across 2 stacked hotfixes");
  delete DATA.hotfix_chain;
  await page.goto(base + "/", { waitUntil: "networkidle0" });
  t("no chain: Fixes tile scope stays 'in this release'",
    (await fixesTileSub()) === "in this release");
  server.close();

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
