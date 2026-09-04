<script>
  // Your setup — the platform matrix that doubles as the picker for the visitor's stack,
  // the component pickers behind a toggle, and the per-setup panel.
  import { flushSync } from "svelte";
  import { app, stackActive, stackComponents, toggleKey, clearStack, shareLink } from "../lib/state.svelte.js";
  import { VERDICTS, VERDICT_TONE, COMP } from "../lib/tables.js";
  import { keyVerdict, stripSummary, isFresh, setupVerdict } from "../lib/verdict.js";
  import { announce } from "../lib/dom.js";
  import Icon from "./Icon.svelte";
  import Mark from "./Mark.svelte";
  import PersonalRisk from "./PersonalRisk.svelte";

  let { pvs } = $props();
  const D = $derived(app.data);
  const active = $derived(stackActive());
  const note = $derived.by(() => {
    const sm = stripSummary(pvs), g = D.recommendation;
    if (isFresh()) return "too new to tell platforms apart — reports are still arriving";
    if (g === "✅") return "clear everywhere";
    if (!sm.clear.length) return "every platform is affected";
    return "clear for " + sm.clear.length + " of " + pvs.length + " platforms";
  });
  const comps = $derived(COMP.map((c) => ({ key: c[0], ic: c[1], label: c[2], kv: keyVerdict([], [c[0]]) })));
  // Components sit behind a small toggle — most visitors only need the platform row.
  let compOpen = $state(stackComponents().length > 0);

  function why(kv) {
    const n = kv.blockers.length;
    return n ? (n + " blocking issue" + (n === 1 ? "" : "s") + " land" + (n === 1 ? "s" : "") + " here")
      : (kv.softened ? "no blocking issue is confirmed here — eases one notch from the global verdict"
      : (kv.fresh ? "fresh release — too new to soften" : "matches the global verdict"));
  }
  function afterStack() {
    if (stackActive()) {
      const svd = setupVerdict();
      announce("For your setup: " + ((VERDICTS[svd.rec] || {}).label || String(svd.rec)));
    } else {
      announce("Setup cleared — showing the global verdict.");
    }
  }
  // Synchronous DOM updates: assistive tech and the suites read the result right after the click.
  function pick(k) { toggleKey(k); flushSync(); afterStack(); }
  function clear() { clearStack(); flushSync(); afterStack(); }
  // The URL always mirrors the stack, so sharing = copying the address. This chip just makes
  // that discoverable; clipboard failure degrades to a hint.
  function share() {
    const link = shareLink();
    const done = () => announce("Link copied — it opens with this setup pre-picked.");
    const fail = () => announce("Couldn't copy automatically — copy the address bar; it carries your setup.");
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(link).then(done, fail);
      else fail();
    } catch (e) { fail(); }
  }
</script>
{#snippet pickBtn(k, ic, label, kv, idx)}{@const v = VERDICTS[kv.rec] || { label: String(kv.rec || "") }}<button class="pick {VERDICT_TONE[kv.rec] || ''}" type="button" style="--i:{idx}" aria-pressed={app.stack[k] ? "true" : "false"} data-k={k} aria-label="{label}: {v.label}" title="{label}: {v.label} — {why(kv)}. Tap to add it to your setup." onclick={() => pick(k)}><Icon k={ic} size={13} /><span class="pk">{label}</span><span class="pv" aria-hidden="true"><Mark rec={kv.rec} /></span></button>{/snippet}
<div class="setup glass" id="setup">
  <div class="sec-head setup-head"><h2 class="sec-title">Your setup</h2><span class="sec-note" id="strip-note">{note}</span></div>
  <div class="setup-intro">Each platform shows its own verdict — tap what you run and the answer below is tuned to your setup (saved on this device).</div>
  <div class="chips"><span class="chip-grp">Platforms &amp; channels</span>{#each pvs as e, idx}{@render pickBtn(e.key, e.ic, e.label, e.kv, idx)}{/each}<button class="pick pick-more" type="button" aria-expanded={compOpen ? "true" : "false"} style="--i:{pvs.length}" aria-controls="comp-chips" title="Also pick the OpenClaw subsystems you rely on" onclick={() => { compOpen = !compOpen; flushSync(); }}>+ components</button><button class="pick pick-clear" type="button" id="stack-clear" style="--i:{pvs.length + 1}" title="Clear every picked platform, channel and component" hidden={!active} onclick={clear}>✕ clear all</button><button class="pick pick-share" type="button" id="stack-share" style="--i:{pvs.length + 2}" title="Copy a link that opens this page with your setup pre-picked" hidden={!active} onclick={share}>⧉ copy link</button></div>
  <div class="chips comp-chips" id="comp-chips" hidden={!compOpen}><span class="chip-grp">Components</span>{#each comps as c, idx}{@render pickBtn(c.key, c.ic, c.label, c.kv, idx)}{/each}</div>
  <PersonalRisk />
</div>
