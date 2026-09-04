<script>
  // The per-setup panel: one row per picked key, judged by the SAME conservative machinery as
  // the strip (keyVerdict), so the panel can never disagree with the chip just tapped. The
  // per-setup VERDICT drives the tone + headline so the copy can't contradict the glyph.
  import { app, stackActive, stackPlatforms, stackComponents } from "../lib/state.svelte.js";
  import { VERDICTS, PLAT_LABEL, COMP_LABEL } from "../lib/tables.js";
  import { keyVerdict, setupVerdict, issuePlatforms, issueComponents, gateBlockers } from "../lib/verdict.js";
  import { issueUrl, listNames } from "../lib/fmt.js";
  import Icon from "./Icon.svelte";
  import Mark from "./Mark.svelte";

  const D = $derived(app.data);
  const active = $derived(stackActive());
  const rows = $derived.by(() => {
    const ki = D.known_issues || [], rs = [];
    stackPlatforms().forEach((k) => {
      const m = PLAT_LABEL[k] || ["globe", k];
      rs.push({ ic: m[0], label: m[1], kv: keyVerdict([k], []), mentions: ki.filter((i) => issuePlatforms(i).indexOf(k) >= 0) });
    });
    stackComponents().forEach((k) => {
      const m = COMP_LABEL[k] || ["build", k];
      rs.push({ ic: m[0], label: m[1], kv: keyVerdict([], [k]), mentions: ki.filter((i) => issueComponents(i).indexOf(k) >= 0) });
    });
    rs.sort((a, b) => b.kv.blockers.length - a.kv.blockers.length || b.mentions.length - a.mentions.length);
    return rs;
  });
  const hot = $derived(rows.filter((r) => r.kv.blockers.length));
  const cool = $derived(rows.filter((r) => !r.kv.blockers.length));
  const names = (rs) => listNames(rs.map((r) => ({ label: r.label })));
  const svd = $derived(setupVerdict());
  const gv = $derived(VERDICTS[svd.global] || { label: String(svd.global || ""), tone: "tone-warn" });
  const sv = $derived(VERDICTS[svd.rec] || { label: String(svd.rec || ""), tone: "tone-warn" });
  const tone = $derived(svd.fresh ? gv.tone : sv.tone);
  const head = $derived.by(() => {
    if (svd.fresh) {
      return "Too new to call for your stack — v" + (D.version || "") + " is a fresh release and "
        + "version-specific reports are still arriving, so we can't clear your setup yet. "
        + "Back up before updating.";
    }
    if (svd.softened) {
      return "Lower risk for your stack — no corroborated blocking issue is confirmed to hit "
        + names(rows) + ". The global “" + gv.label + "” verdict is driven by issues outside your "
        + "setup (or by reports without corroborating traction yet), so for you it eases to "
        + "“" + sv.label + ".” Back up before updating anyway.";
    }
    if (hot.length) {
      const seen = {};
      let n = 0;
      hot.forEach((r) => { r.kv.blockers.forEach((b) => { if (!seen[b.number]) { seen[b.number] = 1; n++; } }); });
      return "The “" + gv.label + "” verdict applies to you — " + n + " blocking issue" + (n === 1 ? "" : "s")
        + " land" + (n === 1 ? "s" : "") + " on " + names(hot)
        + (cool.length ? " (" + names(cool) + " " + (cool.length === 1 ? "is" : "are") + " clear)" : "") + ".";
    }
    return "Your stack looks clear for this release — this matches the global “" + gv.label + "” verdict.";
  });
  // Credible blockers tagged "all" are listed ONCE: they pin every setup only with
  // megathread-class traction (the breadth gate), otherwise through their explicit tags.
  const cross = $derived(gateBlockers().filter((i) => issuePlatforms(i).indexOf("all") >= 0));
</script>
{#snippet links(items, n)}{#each items.slice(0, n) as it, idx}{#if idx}{", "}{/if}<a class="ilink" href={issueUrl(it.number)} target="_blank" rel="noopener noreferrer">#{it.number}</a>{/each}{#if items.length > n}{" +" + (items.length - n) + " more"}{/if}{/snippet}
{#if !active}
  <div class="hint">Nothing picked yet — you're reading the global verdict. Tap the platforms and channels you run for a verdict tuned to your setup.</div>
{:else}
  <div class="risk {tone}">
    <div class="sv"><span class="sv-k">For your setup</span><span class="sv-em" aria-hidden="true"><Mark rec={svd.rec} /></span><span class="sv-l">{sv.label}</span>{#if svd.softened}<span class="sv-note">refined from “{gv.label}” (global)</span>{/if}</div>
    <div class="rh">{head}</div>
    <div class="prow-list">{#each rows as r}{@const nb = r.kv.blockers.length}<div class="prow"><span class="ppn"><Icon k={r.ic} size={13} />{r.label}</span><span class="pbadge {nb ? 'imp-high' : (r.kv.fresh ? 'imp-medium' : 'imp-none')}"><Mark rec={r.kv.rec} size={12} />{nb ? nb + " blocking" : (r.kv.fresh ? "early read" : "clear")}</span>{#if nb}<span class="pdetail">{@render links(r.kv.blockers, 5)}</span>{:else if r.mentions.length}<span class="pdetail muted2">{r.mentions.length} report{r.mentions.length === 1 ? "" : "s"} mention{r.mentions.length === 1 ? "s" : ""} it, none a credible blocker — {@render links(r.mentions, 3)}</span>{:else}<span class="pdetail muted2">no tracked report names it this release</span>{/if}</div>{/each}{#if cross.length}<div class="prow prow-all"><span class="ppn"><Icon k="globe" size={13} />All platforms &amp; channels</span><span class="pbadge imp-medium">{cross.length} cross-cutting</span><span class="pdetail"><span class="ccnote">{"credible blockers not tied to one OS or channel — "}</span>{@render links(cross, 5)}</span></div>{/if}</div>
  </div>
{/if}
