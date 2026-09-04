<script>
  // The four signal tiles. The visible digits are a CSS counter that counts up on first
  // render (.stat .v .num::before); the real value stays as visually hidden text, so the
  // DOM reads "60+" exactly as before.
  import { app } from "../lib/state.svelte.js";
  import { reveal } from "../lib/dom.js";
  const D = $derived(app.data);
  const tiles = $derived.by(() => {
    const ki = D.known_issues || [], ch = D.changes || {};
    const regs = ki.filter((i) => i.category === "regression").length;
    const severe = ki.filter((i) => i.severity === "high" || i.severity === "critical").length;
    const fixes = (ch.fixes || []).length, feats = (ch.features || []).length;
    return [
      { v: ki.length + (D.issues_capped ? "+" : ""), l: "Known issues", s: severe + " high/critical", c: "var(--accent)" },
      { v: regs, l: "Regressions", s: "since release", c: regs ? "var(--bad)" : "var(--good)" },
      { v: fixes, l: "Fixes shipped", s: (D.hotfix_chain || []).length > 1 ? "across " + D.hotfix_chain.length + " stacked hotfixes" : "in this release", c: "var(--good)" },
      { v: feats, l: "Highlights", s: (ch.breaking || []).length + " breaking", c: "var(--accent)" },
    ].map((t) => {
      const m = /^(\d+)(.*)$/.exec(String(t.v)) || [null, "0", String(t.v)];
      return { ...t, v: String(t.v), n: m[1], sfx: m[2].replace(/['\\]/g, "") };
    });
  });
</script>
<div class="stats" use:reveal>{#each tiles as t}<div class="stat" style="--sc:{t.c}"><div class="v" style="--n-final:{t.n};--sfx:'{t.sfx}'"><span class="vh">{t.v}</span><span class="num" aria-hidden="true"></span></div><div class="l">{t.l}</div><div class="s">{t.s}</div></div>{/each}</div>
