// Contrast audit — samples every visible text run on the rendered page, in BOTH themes,
// and asserts WCAG AA: ≥ 4.5:1, or ≥ 3:1 for large text (≥ 24 px, or ≥ 18.66 px bold).
// Drives the REAL template with the shared fixture. NOT part of the hermetic pytest suite
// (the deploy box has no Node) — run on demand:  node tests/browser/contrast.test.js
//
// How the background is found: walk up from the text element, alpha-compositing each
// ancestor's background-color until an opaque one is reached. Surfaces painted with a
// gradient (the card sheen, the best-version card, the evidence toggle, the answer mark)
// are flattened to their base colour by a test-only stylesheet first — those gradients
// add at most a few percent of tint, so the flattened pair is what the browser paints, or
// a touch stricter. Effective opacity is folded into the text colour. Gradient-clipped
// text (color: transparent) and visually hidden text (.vh) are skipped.

const path = require("path");
const fs = require("fs");
const os = require("os");
const { puppeteer, CHROME, pageFor, DATA } = require("./fixture.js");

const FLATTEN = `
  .prose,.ev-card,.flip-card,.why-card,.quote,.catchup,.tcard,.stat,.plat,details.triage,.about{background:var(--card)!important}
  .reco{background:var(--card)!important}
  .details-toggle{background:var(--card)!important}
  .verdict .em{background:var(--card)!important}
  .glass,.topbar{-webkit-backdrop-filter:none!important;backdrop-filter:none!important}
  *{transition:none!important;animation:none!important}
  .reveal{opacity:1!important;transform:none!important}
`;

// Runs in the page: returns [{sel, text, fg, bg, ratio, size, weight, need}] for every
// text run whose ratio is below its threshold, plus the number of runs sampled.
function auditInPage() {
  function parse(s) {
    let m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(s);
    if (m) return [+m[1], +m[2], +m[3], m[4] == null ? 1 : +m[4]];
    m = /color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\)/.exec(s);
    if (m) {
      const a = m[4] == null ? 1 : (m[4].endsWith("%") ? parseFloat(m[4]) / 100 : +m[4]);
      return [+m[1] * 255, +m[2] * 255, +m[3] * 255, a];
    }
    if (s === "transparent") return [0, 0, 0, 0];
    return null;
  }
  const over = (top, under) => {                       // top over under, both [r,g,b,a]
    const a = top[3] + under[3] * (1 - top[3]);
    if (!a) return [0, 0, 0, 0];
    return [0, 1, 2].map((i) => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a).concat([a]);
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
  const hex = (c) => "#" + [0, 1, 2].map((i) => Math.round(c[i]).toString(16).padStart(2, "0")).join("");
  const selOf = (el) => {
    const bits = [];
    for (let n = el, i = 0; n && n !== document.body && i < 4; n = n.parentElement, i++) {
      bits.unshift(n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") +
        (n.classList.length ? "." + Array.from(n.classList).slice(0, 2).join(".") : ""));
    }
    return bits.join(" > ");
  };

  const failures = [];
  let sampled = 0;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "TITLE", "OPTION", "SVG"]);
  for (const el of document.querySelectorAll("body *")) {
    if (SKIP_TAGS.has(el.tagName) || el.closest("svg") || el.closest(".vh, .skip-link")) continue;
    const text = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
    if (!text) continue;
    if (el.closest("[hidden]") || !el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden") continue;
    let fg = parse(cs.color);
    if (!fg || fg[3] === 0) continue;                   // gradient-clipped or invisible text

    // effective opacity + background stack, element → html
    let opacity = 1;
    const stack = [];
    for (let n = el; n; n = n.parentElement) {
      const ncs = getComputedStyle(n);
      opacity *= parseFloat(ncs.opacity || "1");
      stack.push(parse(ncs.backgroundColor) || [0, 0, 0, 0]);
      if (n === document.documentElement) break;
    }
    if (opacity < 0.1) continue;                        // hidden until hover (permalink #), not a text surface
    let bg = [255, 255, 255, 1];                        // beyond html: the viewer's ground; html/body are opaque anyway
    for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
    fg = over([fg[0], fg[1], fg[2], fg[3] * opacity], bg);

    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    sampled++;
    if (r + 1e-9 < need) failures.push({ sel: selOf(el), text: text.slice(0, 40), fg: hex(fg), bg: hex(bg),
                                          ratio: +r.toFixed(2), size: +size.toFixed(1), weight, need });
  }
  return { sampled, failures };
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const tmp = path.join(os.tmpdir(), "contrast_test.html");
  fs.writeFileSync(tmp, pageFor(DATA));
  let bad = 0;
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage();
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    await page.evaluateOnNewDocument((t) => { try { localStorage.setItem("oc-theme", t); } catch {} }, theme);
    await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: theme },
                                     { name: "prefers-reduced-motion", value: "reduce" }]);
    await page.setViewport({ width: 1280, height: 900 });
    await page.goto("file://" + tmp, { waitUntil: "networkidle0" });
    await page.addStyleTag({ content: FLATTEN });
    // Show everything the page can show: the evidence, every long-tail panel, the
    // component pickers, the review panel, a few expanded issue rows, the triage box.
    await page.evaluate(() => {
      const b = document.querySelector(".details-toggle");
      if (b && b.getAttribute("aria-expanded") !== "true") b.click();
      document.querySelectorAll(".ltab-panel[hidden], #comp-chips[hidden], .rev-detail[hidden]").forEach((n) => n.removeAttribute("hidden"));
      Array.from(document.querySelectorAll(".idetail[hidden]")).slice(0, 3).forEach((n) => n.removeAttribute("hidden"));
      document.querySelectorAll("details.triage").forEach((d) => d.setAttribute("open", ""));
    });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    const { sampled, failures } = await page.evaluate(auditInPage);
    const themeName = theme.toUpperCase();
    if (errs.length) { console.log(`✗ ${themeName}: page errors ${JSON.stringify(errs)}`); bad++; }
    if (sampled < 150) { console.log(`✗ ${themeName}: only ${sampled} text runs sampled — the audit didn't see the page`); bad++; }
    if (failures.length) {
      bad++;
      console.log(`✗ ${themeName}: ${failures.length} of ${sampled} text runs below AA`);
      failures.sort((a, b) => a.ratio - b.ratio).slice(0, 40).forEach((f) =>
        console.log(`    ${f.ratio}:1 (need ${f.need})  ${f.fg} on ${f.bg}  ${f.size}px/${f.weight}  ${f.sel}  “${f.text}”`));
      if (failures.length > 40) console.log(`    … ${failures.length - 40} more`);
    } else {
      console.log(`✓ ${themeName}: ${sampled} text runs, every one ≥ AA (4.5:1, or 3:1 for large text)`);
    }
    await page.close();
  }
  await browser.close();
  if (bad) { console.log(`\n${bad} FAILED`); process.exit(1); }
  console.log("\nContrast audit passed in both themes");
})().catch((e) => { console.error(e); process.exit(1); });
