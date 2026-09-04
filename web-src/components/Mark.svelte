<script>
  // A verdict mark: the page's own glyph for ✅ ⚠️ ⏸️ ⏳, plus the emoji itself as visually
  // hidden text right beside it. The emoji is the data key shared with SSR / llms.txt / the
  // badge / RSS, and the browser suites read it back through textContent — never drop it.
  import { VGLYPH, VGLYPH_UNKNOWN } from "../lib/tables.js";
  let { rec, size = 16 } = $props();
</script>
<svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" stroke-width={rec === "⏸️" ? "3.2" : "2.3"} stroke-linecap="round" stroke-linejoin="round" class="vg" aria-hidden="true" focusable="false"><path d={VGLYPH[rec] || VGLYPH_UNKNOWN}></path></svg><span class="vh">{String(rec ?? "")}</span>
