<script>
  // Issue-triage activity from Clawsweeper, OpenClaw's triage bot.
  import { app } from "../lib/state.svelte.js";
  import { hasTriage } from "../lib/sections.js";
  import { issueUrl } from "../lib/fmt.js";
  import { reveal } from "../lib/dom.js";
  import SectionHead from "./SectionHead.svelte";
  const D = $derived(app.data);
  const work = $derived(D.clawsweeper_work || []);
  const closed = $derived(D.clawsweeper_closed || []);
  const cols = $derived([{ title: "🛠 In progress", items: work }, { title: "✓ Recently closed", items: closed }]);
</script>
{#if hasTriage(D)}<div class="section" id="issue-triage-activity" use:reveal><SectionHead title="Issue-triage activity" note="from Clawsweeper, OpenClaw's triage bot" id="issue-triage-activity" /><details class="triage"><summary>{"🧹  " + work.length + " in progress · " + closed.length + " recently closed"}<span class="caret">▶</span></summary><div class="triage-body">{#each cols as col}<div class="triage-col"><h4>{col.title + " (" + col.items.length + ")"}</h4><div class="tscroll">{#each col.items as it}<div class="trow"><a class="tn" href={issueUrl(it.number)} target="_blank" rel="noopener noreferrer">#{it.number}</a><span class="tt">{it.title || ""}</span></div>{/each}</div></div>{/each}</div></details></div>{/if}
