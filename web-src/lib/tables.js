// Lookup tables shared by every component. Keys and labels are the page's contract with
// the server side (render.py, llms.txt, the badge) and the browser suites — change with care.

// Mirrors verdict.STATUS (Python) — ONE name per verdict on every surface.
export const VERDICTS = {
  "✅": { label: "Safe to update", tone: "tone-good" },
  "⚠️": { label: "Update with care", tone: "tone-warn" },
  "⏸️": { label: "Skip this version", tone: "tone-bad" },
};
export const VERDICT_ORDER = ["✅", "⚠️", "⏸️"];   // increasing caution
export const VERDICT_TONE = { "✅": "pv-good", "⚠️": "pv-warn", "⏸️": "pv-bad" };
// Risk height (%) per verdict for the release-health trend — taller = riskier.
export const RISK = { "✅": 18, "⚠️": 52, "⏸️": 100 };

// Tables carry [key, iconKey, label] — iconKey resolves via ICONS (inline stroke SVGs).
export const CAT = {
  regression: { icon: "regression", label: "Regression", cls: "t-bad", desc: "Confirmed regression — worked before, broken by a recent release." },
  post_release: { icon: "post", label: "Post-release", cls: "t-warn", desc: "Filed after this release and affects it, but not confirmed as a regression." },
  diamond_lobster: { icon: "gem", label: "Diamond Lobster", cls: "t-info", desc: "Maintainers' '🦞 diamond lobster' quality rating — a notable issue (not a severity)." },
  active: { icon: "active", label: "Ongoing", cls: "t-warn", desc: "Open and ongoing — a long-standing issue not specific to this release." },
};
export const PLAT = [
  ["windows", "windows", "Windows"], ["macos", "macos", "macOS"], ["linux", "linux", "Linux"],
  ["ios", "ios", "iOS"], ["android", "android", "Android"], ["web", "web", "Web UI"],
  ["discord", "discord", "Discord"], ["slack", "slack", "Slack"], ["telegram", "telegram", "Telegram"],
  ["whatsapp", "whatsapp", "WhatsApp"], ["other-channel", "otherchan", "Other channels"],
];
export const PLAT_LABEL = {};
PLAT.forEach((p) => { PLAT_LABEL[p[0]] = [p[1], p[2]]; });
// Components = which subsystem of OpenClaw an issue touches (orthogonal to platforms).
export const COMP = [
  ["gateway", "gateway", "Gateway"], ["models", "models", "Models"], ["memory", "memory", "Memory"],
  ["sessions", "sessions", "Sessions"], ["auth", "auth", "Auth"], ["channels", "channels", "Channels"],
  ["plugins", "plugins", "Plugins"], ["agents", "agents", "Agents"], ["tasks", "tasks", "Tasks"],
  ["tools", "tools", "Tools"], ["build", "build", "Build"],
];
export const COMP_LABEL = {};
COMP.forEach((c) => { COMP_LABEL[c[0]] = [c[1], c[2]]; });
export const IMPACT = { none: { label: "None", w: 10 }, low: { label: "Low", w: 38 }, medium: { label: "Medium", w: 68 }, high: { label: "High", w: 100 } };
export const SEVW = { critical: 4, high: 3, medium: 2, low: 1 };

// Title keyword match — the FALLBACK for issues with no analyst `platforms` tag.
export const STACK_MATCH = {
  windows: /windows|win32|\.exe|powershell/i,
  macos: /macos|mac os|imessage|apple|darwin/i,
  linux: /linux|docker|systemd|ubuntu|debian|cgroup/i,
  ios: /\bios\b|iphone|ipad/i,
  android: /android|termux/i,
  web: /web[ -]?ui/i,
  discord: /discord/i,
  slack: /slack/i,
  telegram: /telegram/i,
  whatsapp: /whatsapp|baileys/i,
  "other-channel": /msteams|wechat|bluebubbles|mattermost|nostr|feishu|zalo|twitch|nextcloud|synology|qqbot|\bsms\b/i,
};
export const PLAT_KEYS = { windows: 1, macos: 1, linux: 1, ios: 1, android: 1, web: 1, discord: 1, slack: 1, telegram: 1, whatsapp: 1, "other-channel": 1, all: 1 };

// Confidence is categorical (low/medium/high) — a plain-language note so "medium" isn't a mystery.
export const CONF_DESC = {
  high: "Strong evidence — the data is consistent and fairly complete.",
  medium: "Some evidence gaps remain — treat the verdict as a moderate-strength signal.",
  low: "Limited or conflicting data — treat as a weak signal and verify against the issues.",
};
// On a fresh release the evidence is NOT complete yet — the caption must not claim completeness.
export const CONF_DESC_FRESH = {
  high: "Strong on the evidence so far — but this is a fresh release, so it isn't fully reported yet; the picture keeps filling in over the next few runs.",
  medium: "Some evidence gaps remain, and a fresh release isn't fully reported yet — treat it as a moderate-strength early read.",
  low: "Limited or conflicting data on a freshly-dropped release — treat as a weak early signal and verify against the issues.",
};

export const TR_BADGE = {
  held: { txt: "✓ held", cls: " tr-held", tip: "Every assessment of this version reached the same verdict." },
  hardened: { txt: "↓ hardened", cls: " tr-hard", tip: "The verdict got more cautious as post-release reports accrued — the expected direction for an immutable release that turns out worse than its first read." },
  softened: { txt: "↑ softened", cls: " tr-soft", tip: "The verdict got LESS cautious over time — rare, and it needs a real cause (a severe report debunked, or the early read was over-cautious). Check the runs' evidence before trusting it." },
  mixed: { txt: "~ moved", cls: "", tip: "The verdict moved during the window but ended where it started." },
  single: { txt: "1 read", cls: "", tip: "Assessed once so far — no evolution to compare yet." },
};

// This app's own repo — the report-a-problem path (keep in sync w/ config.APP_REPO_URL).
export const REPO = "https://github.com/camilla-oclm/openclaw_status_app";

// Inline icon set — monochrome stroke SVGs (currentColor). Verdict emoji (the ✅/⚠️/⏸️ data
// keys) stay text everywhere — they are the cross-surface identity shared with SSR /
// llms.txt / badge / RSS.
export const ICONS = {
  windows: "M4 4h16v16H4z M12 4v16 M4 12h16",
  macos: "M9 9V5.5A2.5 2.5 0 1 0 6.5 9H9zm0 0v6m0-6h6m-6 6H6.5A2.5 2.5 0 1 0 9 17.5V15zm6-6V5.5A2.5 2.5 0 1 1 17.5 9H15zm0 0v6m0 0v2.5a2.5 2.5 0 1 0 2.5-2.5H15z",
  linux: "M4 5h16v14H4z M7.5 9.5l3 2.5-3 2.5 M12.5 14.5H17",
  ios: "M8 2.5h8a2 2 0 0 1 2 2v15a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2z M10.5 18.5h3",
  android: "M6 16a6 6 0 0 1 12 0v1.5H6V16z M9.75 13h.01 M14.25 13h.01 M8.5 11L7 8 M15.5 11L17 8",
  web: "M3 5h18v14H3z M3 9h18 M6 7h.01 M8.5 7h.01",
  whatsapp: "M12 3a9 9 0 0 0-7.8 13.4L3 21l4.6-1.2A9 9 0 1 0 12 3z M9.2 8.4l1.3 2.2-.9 1.1a7.5 7.5 0 0 0 2.7 2.7l1.1-.9 2.2 1.3-.9 1.8c-3.6-.4-6.9-3.7-7.3-7.3l1.8-.9z",
  otherchan: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  discord: "M21 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.6 0-3.1-.4-4.4-1.2L3 20l1.2-5.1A8.5 8.5 0 1 1 21 11.5z",
  slack: "M10 3L8 21 M16 3l-2 18 M3.5 9H21 M3 15h17.5",
  telegram: "M22 2L11 13 M22 2L15 22l-4-9-9-4 20-7z",
  gateway: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M12 3v6 M12 15v6 M3 12h6 M15 12h6",
  models: "M9 9h6v6H9z M5 5h14v14H5z M9 2v3 M15 2v3 M9 19v3 M15 19v3 M2 9h3 M2 15h3 M19 9h3 M19 15h3",
  memory: "M12 8c4.4 0 8-1.1 8-2.5S16.4 3 12 3 4 4.1 4 5.5 7.6 8 12 8z M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13 M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5",
  sessions: "M12 3l9 5-9 5-9-5 9-5z M3 13.5l9 5 9-5",
  auth: "M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z M9 11V7a3 3 0 0 1 6 0v4",
  channels: "M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M16.2 7.8a6 6 0 0 1 0 8.4 M7.8 16.2a6 6 0 0 1 0-8.4 M19.1 4.9a10 10 0 0 1 0 14.2 M4.9 19.1a10 10 0 0 1 0-14.2",
  plugins: "M9 7V2.5 M15 7V2.5 M7 7h10v4a5 5 0 0 1-10 0V7z M12 16v5.5",
  agents: "M6 8h12a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z M12 8V4 M9.5 13.5h.01 M14.5 13.5h.01",
  tasks: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3.5 2",
  tools: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
  build: "M21 8l-9-5-9 5v8l9 5 9-5V8z M3 8l9 5 9-5 M12 13v8",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M3 12h18 M12 3a13.9 13.9 0 0 1 0 18 M12 3a13.9 13.9 0 0 0 0 18",
  regression: "M2 7l7.5 7.5 5-5L22 17 M16 17h6v-6",
  post: "M4 21V4c3.5-2 7 2 12 0v9c-5 2-8.5-2-12 0",
  gem: "M6 3h12l4 6-10 12L2 9l4-6z M2 9h20 M9.5 3L8 9l4 12 M14.5 3L16 9l-4 12",
  active: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 13.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z",
  doc: "M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z M14 2v6h6",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  review: "M12 3v18 M6 21h12 M3 7h18 M6 7l-3.5 7.5a3.5 3.5 0 0 0 7 0L6 7z M18 7l-3.5 7.5a3.5 3.5 0 0 0 7 0L18 7z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
};

// Verdict glyphs — the page's own marks for the four verdict keys, drawn in the icon set's
// stroke language and tinted by the surrounding tone (currentColor). The emoji is NOT
// replaced: it stays in the DOM as visually hidden text beside the glyph (see Mark.svelte).
export const VGLYPH = {
  "✅": "M4.5 12.5l4.8 4.8L19.5 7",
  "⚠️": "M12 4L2.8 19.6h18.4L12 4z M12 10v4.2 M12 17.2h.01",
  "⏸️": "M8.5 5.5v13 M15.5 5.5v13",
  "⏳": "M6 3.5h12 M6 20.5h12 M7.5 3.5v3a4.5 4.5 0 0 0 2 3.7L12 12l-2.5 1.8a4.5 4.5 0 0 0-2 3.7v3 M16.5 3.5v3a4.5 4.5 0 0 1-2 3.7L12 12l2.5 1.8a4.5 4.5 0 0 1 2 3.7v3",
};
export const VGLYPH_UNKNOWN = "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M9.6 9.6a2.4 2.4 0 1 1 3.4 2.2c-.7.4-1 1-1 1.7 M12 17h.01";
