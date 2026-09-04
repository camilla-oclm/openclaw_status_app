<script>
  // A section header: the permalink "#" (copies the section URL; the hash still navigates),
  // the title, an optional count pill and an optional note.
  let { title, count = null, note = "", id } = $props();
  let copied = $state(false);
  let timer = 0;
  function copy() {
    const url = location.origin + location.pathname + "#" + id;
    try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url); } catch (e) {}
    copied = true;
    clearTimeout(timer);
    timer = setTimeout(() => { copied = false; }, 1200);
  }
</script>
<div class="sec-head"><a class="sec-anchor {copied ? 'copied' : ''}" href="#{id}" title="Copy link to this section" aria-label="Permalink: {title}" onclick={copy}>{copied ? "✓" : "#"}</a><h2 class="sec-title">{title}</h2>{#if count != null}<span class="sec-count">{count}</span>{/if}{#if note}<span class="sec-note">{note}</span>{/if}</div>
