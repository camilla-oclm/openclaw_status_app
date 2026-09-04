#!/usr/bin/env node
// Screenshot a page — a local file or a URL — with puppeteer-core and a system Chromium.
// Dev tool for the design loop; pairs with tools/preview.py. Not part of any test suite.
//
//   node tools/shot.cjs web/proto/preview.html shots/desktop-dark.png
//   node tools/shot.cjs "web/proto/preview.html?stack=linux,discord,gateway" shots/picked.png --theme light
//   node tools/shot.cjs web/proto/preview.html shots/mobile-dark.png --size 390x844 --dpr 2 --full
//   node tools/shot.cjs web/proto/preview.html shots/evidence.png --open --scroll "#details-body"
//   node tools/shot.cjs web/proto/preview.html shots/tab-2.png --open \
//        --click '.ltabs[role="tablist"] .ltab:nth-child(2)' --scroll '.ltabs[role="tablist"]'
//   node tools/shot.cjs https://clawstat.us shots/live.png
//   node tools/shot.cjs web/logo.svg web/logo-512.png --size 512x512 --transparent
//
// Options
//   --size WxH          viewport in CSS px (default 1440x900)
//   --dpr N             device pixel ratio (default 1; the README heroes use 2)
//   --theme dark|light  the page's stored theme + prefers-color-scheme (default dark)
//   --full              capture the full page (default: the viewport)
//   --open              open the evidence (clicks .details-toggle when it is collapsed)
//   --click SEL         click SEL after load — repeatable, applied in order; an in-page
//                       click(), so it also works on nodes hidden behind the toggle
//   --scroll SEL        scroll SEL to the top of the viewport before capturing
//   --wait MS           settle time after the actions (default 300)
//   --motion            keep animations; by default prefers-reduced-motion: reduce is
//                       emulated so every .reveal is visible and captures are deterministic
//   --allow-errors      exit 0 even when the page threw an uncaught error
//   --transparent       omit the page background (icons/marks rendered from an .svg)
//
// A local file is served over http by an in-process static server whose roots are the
// file's own directory, then web/. That way the template's relative fonts/… and absolute
// /logo.svg, /sticker.png resolve exactly as in production (file:// would block the font),
// and the page's runtime fetch of latest.json finds the copy tools/preview.py wrote.
//
// The puppeteer / Chromium discovery is the same as tests/browser/*.test.js:
// PUPPETEER_PATH and CHROME_PATH win, then an npx-cached puppeteer, a repo-local
// puppeteer{,-core}, a ~/.cache/puppeteer Chrome, then system Chromium.

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const REPO_ROOT = path.join(__dirname, "..");

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
const puppeteer = require(process.env.PUPPETEER_PATH ||
  firstUnder(path.join(os.homedir(), ".npm", "_npx"), path.join("node_modules", "puppeteer")) ||
  firstExisting([path.join(REPO_ROOT, "node_modules", "puppeteer"),
                 path.join(REPO_ROOT, "node_modules", "puppeteer-core")]) ||
  "puppeteer");
const CHROME = process.env.CHROME_PATH ||
  firstUnder(path.join(os.homedir(), ".cache", "puppeteer", "chrome"),
             path.join("chrome-linux64", "chrome")) ||
  firstExisting(["/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]);

// ── args ────────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const o = { size: "1440x900", dpr: 1, theme: "dark", full: false, open: false,
              clicks: [], scroll: null, wait: 300, motion: false, allowErrors: false, transparent: false, pos: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], next = () => argv[++i];
    if (a === "--size") o.size = next();
    else if (a === "--dpr") o.dpr = Number(next());
    else if (a === "--theme") o.theme = next();
    else if (a === "--full") o.full = true;
    else if (a === "--open") o.open = true;
    else if (a === "--click") o.clicks.push(next());
    else if (a === "--scroll") o.scroll = next();
    else if (a === "--wait") o.wait = Number(next());
    else if (a === "--motion") o.motion = true;
    else if (a === "--allow-errors") o.allowErrors = true;
    else if (a === "--transparent") o.transparent = true;
    else if (a === "-h" || a === "--help") { usage(); process.exit(0); }
    else if (a.startsWith("--")) { console.error(`unknown option ${a}`); usage(); process.exit(2); }
    else o.pos.push(a);
  }
  if (o.pos.length !== 2) { usage(); process.exit(2); }
  const m = /^(\d+)x(\d+)$/.exec(o.size);
  if (!m) { console.error(`--size wants WxH, got ${o.size}`); process.exit(2); }
  if (!["dark", "light"].includes(o.theme)) { console.error("--theme is dark or light"); process.exit(2); }
  return { ...o, target: o.pos[0], out: o.pos[1], width: Number(m[1]), height: Number(m[2]) };
}
function usage() {
  const src = fs.readFileSync(__filename, "utf8").split("\n");
  console.error(src.slice(1, src.findIndex((l, i) => i > 1 && !l.startsWith("//"))).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
}

// ── a tiny static server: roots tried in order, path-traversal safe ─────────────
const MIME = { ".html": "text/html; charset=utf-8", ".json": "application/json", ".js": "text/javascript",
               ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
               ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain; charset=utf-8",
               ".xml": "application/xml", ".webp": "image/webp", ".jpg": "image/jpeg" };
function serve(roots) {
  const server = http.createServer((req, res) => {
    let rel;
    try { rel = decodeURIComponent(new URL(req.url, "http://x").pathname); } catch { rel = "/"; }
    if (rel.endsWith("/")) rel += "index.html";
    for (const root of roots) {
      const abs = path.resolve(root, "." + rel);
      if (!abs.startsWith(root + path.sep) && abs !== root) continue;
      if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
        res.writeHead(200, { "content-type": MIME[path.extname(abs).toLowerCase()] || "application/octet-stream",
                             "cache-control": "no-store" });
        fs.createReadStream(abs).pipe(res);
        return;
      }
    }
    res.writeHead(404); res.end("not found");
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

// ── main ────────────────────────────────────────────────────────────────────────
(async () => {
  const opt = parseArgs(process.argv.slice(2));
  let url = opt.target, server = null;
  if (!/^https?:\/\//.test(opt.target)) {
    const [file, query = ""] = opt.target.split("?");
    const abs = path.resolve(file);
    if (!fs.existsSync(abs)) { console.error(`no such file: ${abs}`); process.exit(2); }
    server = await serve([path.dirname(abs), path.join(REPO_ROOT, "web")]);
    url = `http://127.0.0.1:${server.address().port}/${path.basename(abs)}${query ? "?" + query : ""}`;
  }
  if (!CHROME && !process.env.PUPPETEER_PATH) console.error("warning: no Chromium found — set CHROME_PATH");

  const browser = await puppeteer.launch({ executablePath: CHROME || undefined, headless: "new",
                                           args: ["--no-sandbox", "--hide-scrollbars"] });
  const errors = [], consoleErrors = [];
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    await page.setViewport({ width: opt.width, height: opt.height, deviceScaleFactor: opt.dpr });
    const features = [{ name: "prefers-color-scheme", value: opt.theme }];
    if (!opt.motion) features.push({ name: "prefers-reduced-motion", value: "reduce" });
    await page.emulateMediaFeatures(features);
    // The page reads its stored theme before the system preference — set both.
    await page.evaluateOnNewDocument((t) => { try { localStorage.setItem("oc-theme", t); } catch {} }, opt.theme);

    await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 });
    await page.evaluate(() => document.fonts && document.fonts.ready);

    if (opt.open) {
      await page.evaluate(() => {
        const b = document.querySelector(".details-toggle");
        if (b && b.getAttribute("aria-expanded") !== "true") b.click();
      });
    }
    for (const sel of opt.clicks) {
      const hit = await page.evaluate((s) => { const n = document.querySelector(s); if (n) n.click(); return !!n; }, sel);
      if (!hit) console.error(`warning: --click ${sel} matched nothing`);
      await new Promise((r) => setTimeout(r, 150));
    }
    if (opt.scroll) {
      const hit = await page.evaluate((s) => {
        const n = document.querySelector(s); if (n) n.scrollIntoView({ block: "start" }); return !!n;
      }, opt.scroll);
      if (!hit) console.error(`warning: --scroll ${opt.scroll} matched nothing`);
    }
    await new Promise((r) => setTimeout(r, opt.wait));

    fs.mkdirSync(path.dirname(path.resolve(opt.out)), { recursive: true });
    await page.screenshot({ path: opt.out, fullPage: opt.full, omitBackground: opt.transparent });
    const docH = await page.evaluate(() => document.documentElement.scrollHeight);
    const kb = (fs.statSync(opt.out).size / 1024).toFixed(0);
    console.log(`wrote ${opt.out} — ${opt.width}x${opt.full ? docH : opt.height} css px @${opt.dpr}x, ` +
                `${opt.theme}${opt.open ? ", evidence open" : ""}, ${kb} KB; document ${docH} px tall`);
  } finally {
    await browser.close();
    if (server) server.close();
  }
  for (const e of consoleErrors) console.error(`console.error: ${e}`);
  for (const e of errors) console.error(`PAGE ERROR: ${e}`);
  if (errors.length && !opt.allowErrors) process.exit(1);
})().catch((e) => { console.error(e); process.exit(1); });
