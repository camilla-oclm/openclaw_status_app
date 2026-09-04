<script>
  // What is this? Independence + how the verdict is made — so a first-time or skeptical
  // visitor learns it's an unofficial automated tool.
  import { app } from "../lib/state.svelte.js";
  import { REPO } from "../lib/tables.js";
  import { reportUrl } from "../lib/fmt.js";
  const D = $derived(app.data);
</script>
<div class="about">
  <span class="ai">ℹ️</span>
  <div class="atxt">
    <p><b>{"What is this? "}</b>An independent, unofficial tracker — <b>not</b> affiliated with OpenClaw. It reads post-release bug reports from the public <a href="https://github.com/openclaw/openclaw/issues" target="_blank" rel="noopener noreferrer">OpenClaw issue tracker</a>, scores them by the project's own severity labels, and has two different LLMs — an analyst and an independent validator — weigh the evidence. No human writes the verdict, so sanity-check it against the linked issues.</p>
    <p class="alinks"><a href={REPO} target="_blank" rel="noopener noreferrer">How it works / source ↗</a><a href="llms.txt" target="_blank" rel="noopener noreferrer">For AI agents (llms.txt) ↗</a><a href={reportUrl(D)} target="_blank" rel="noopener noreferrer" title="Something looks wrong? File it — the report opens prefilled with this page's version, verdict and timestamp.">Report a problem ↗</a></p>
  </div>
  <img class="about-sticker" src="/sticker.png" loading="lazy" alt="ClawStat.us — the OpenClaw release-status lobster">
</div>
