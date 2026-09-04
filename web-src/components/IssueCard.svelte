<script>
  // One known-issue row: the compact one-liner, and the expanded panel with the full title,
  // the tag set and the GitHub link.
  import { flushSync } from "svelte";
  import { CAT, PLAT_LABEL, COMP_LABEL } from "../lib/tables.js";
  import { issuePlatforms, issueComponents } from "../lib/verdict.js";
  import { issueUrl, fmtDate, timeAgo, cap } from "../lib/fmt.js";
  import Icon from "./Icon.svelte";
  let { i, mine, visible } = $props();
  let open = $state(false);
  const sev = $derived(String(i.severity || "medium"));
  const cat = $derived(CAT[i.category] || { icon: "doc", label: cap(i.category || "issue"), cls: "" });
  const ips = $derived(issuePlatforms(i));
  const comps = $derived(issueComponents(i));
  const dec = $derived(i.clawsweeper_decision);
  const sevCls = $derived((sev === "critical" || sev === "high") ? "t-bad" : (sev === "medium" ? "t-warn" : ""));
  const num = $derived("#" + i.number);
  const detId = $derived("idet-" + i.number);
</script>
<div class="issue sev-{sev}{mine ? ' mine' : ''}" data-cat={i.category || "other"} data-mine={mine ? "1" : "0"} data-comp={comps.join(" ")} style:display={visible ? "" : "none"}>
  <button class="irow" type="button" aria-expanded={open ? "true" : "false"} aria-controls={detId} title="Show details for {num}" onclick={() => { open = !open; flushSync(); }}><span class="sevdot" title="{sev} severity"></span><span class="inum">{num}</span><span class="ititle">{i.title || "(untitled)"}</span><span class="ibs">{#if i.is_new}<span class="ib t-good" title="New since last run">🆕</span>{/if}{#if mine}<span class="ib t-me" title="Affects your stack">★</span>{/if}<span class="ib" title={cat.label}><Icon k={cat.icon} size={13} /></span>{#if i.reactions}<span class="ib">{"👍 " + i.reactions}</span>{/if}</span><span class="chev" aria-hidden="true">▸</span></button>
  <div class="idetail" id={detId} hidden={!open}><div class="idet-title">{i.title || "(untitled)"}</div><div class="tags">{#if i.is_new}<span class="tag t-good">🆕 new</span>{/if}{#if mine}<span class="tag t-me">★ your stack</span>{/if}<span class="tag {cat.cls}" title={cat.desc || ""}><Icon k={cat.icon} size={12} />{cat.label}</span>{#if ips.indexOf("all") >= 0}<span class="tag t-plat"><Icon k="globe" size={12} />All platforms</span>{:else}{#each ips.slice(0, 4) as pk}{#if PLAT_LABEL[pk]}<span class="tag t-plat"><Icon k={PLAT_LABEL[pk][0]} size={12} />{PLAT_LABEL[pk][1]}</span>{/if}{/each}{/if}{#each comps as ck}{#if COMP_LABEL[ck]}<span class="tag t-comp"><Icon k={COMP_LABEL[ck][0]} size={12} />{COMP_LABEL[ck][1]}</span>{/if}{/each}{#if i.affects_version}<span class="tag t-bad">⊘ affects this version</span>{/if}{#if dec && dec !== "unknown"}<span class="tag {dec === 'keep_open' ? 't-warn' : 't-good'}" title="Clawsweeper (OpenClaw's automated issue-triage bot) {dec === 'keep_open' ? 'kept this open — not auto-closed or resolved.' : 'marked this ' + dec + '.'}">{dec === "keep_open" ? "⊙ keep open" : "✓ " + dec}</span>{/if}{#if i.fixed_in}<span class="tag t-good">{"✓ fixed in " + i.fixed_in}</span>{:else if String(i.state || "").toLowerCase() === "closed"}<span class="tag t-good" title="Upstream closed this as completed — the fix is merged for a future release but almost certainly NOT in this one.">✓ fix merged upstream</span>{:else if i.category === "regression" || i.category === "post_release"}<span class="tag t-bad">● unfixed</span>{/if}{#if i.has_workaround}<span class="tag t-info" title="The issue report mentions a workaround or mitigation — open the linked thread for details. (Detected from the report text; not a guarantee one exists or is official.)">🔧 workaround noted</span>{/if}{#if i.reactions}<span class="tag">{"👍 " + i.reactions}</span>{/if}<span class="tag {sevCls}">{sev + " severity"}</span>{#if i.created_at}<span class="tag" title="When this issue was opened on GitHub">{"opened " + fmtDate(i.created_at)}</span>{:else if i.first_seen}<span class="tag">{"first tracked " + timeAgo(i.first_seen)}</span>{/if}{#if i.tag_source && i.tag_source !== "analyst"}<span class="tag t-auto" title="The platform/subsystem tags here were auto-detected from the issue's text and labels — not asserted by the analyst. Verify on GitHub.">🔍 auto-detected</span>{/if}{#each i.labels || [] as lbl}<span class="tag tag-raw" title="Maintainer-assigned GitHub label">{String(lbl)}</span>{/each}</div><a class="idet-link" href={issueUrl(i.number)} target="_blank" rel="noopener noreferrer">{"View " + num + " on GitHub ↗"}</a></div>
</div>
