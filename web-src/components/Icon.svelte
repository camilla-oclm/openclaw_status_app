<script>
  // One inline stroke icon from the ICONS set (currentColor); unknown keys draw the document.
  import { ICONS } from "../lib/tables.js";
  let { k, size = 14 } = $props();
</script>
<svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="ic-svg" aria-hidden="true" focusable="false"><path d={ICONS[k] || ICONS.doc}></path></svg>
