<script>
  import { app } from "../lib/state.svelte.js";
  import { fmtDate, reportUrl } from "../lib/fmt.js";
  const D = $derived(app.data);
  const u = $derived(D.usage || {});
</script>
<footer class="foot">
  <div class="how"><span class="fi">🤖</span><span>This verdict is generated automatically: a data collector pulls GitHub releases, post-release bug reports, Clawsweeper triage and community chatter, then a two-model LLM pipeline (analyst + validator) weighs the evidence. No human edits the result — always sanity-check against the linked issues.</span></div>
  <div class="meta">{#if D.assessed_at}<span>Assessed <code>{fmtDate(D.assessed_at)}</code></span>{/if}{#if u.api_calls}<span>Pipeline <code>{u.api_calls + " model calls"}</code></span>{/if}{#if u.tokens_in}<span>Tokens <code>{u.tokens_in + "→" + (u.tokens_out || 0)}</code></span>{/if}<a href="https://github.com/openclaw/openclaw/releases" target="_blank" rel="noopener noreferrer">Releases ↗</a><a href="latest.json" target="_blank" rel="noopener noreferrer">JSON API ↗</a><a href="llms.txt" target="_blank" rel="noopener noreferrer">llms.txt (for AI agents) ↗</a><a href={reportUrl(D)} target="_blank" rel="noopener noreferrer" title="Something on this page looks wrong? File it — the report opens prefilled with this page's version, verdict and timestamp.">Report a problem ↗</a></div>
  <div class="disclaimer">Independent, unofficial project — not affiliated with, or endorsed by, OpenClaw or its maintainers. Verdicts are generated automatically and may be wrong; confirm against the linked issues before you update. Data is drawn from the public github.com/openclaw/openclaw issue tracker and releases via the GitHub API.</div>
</footer>
