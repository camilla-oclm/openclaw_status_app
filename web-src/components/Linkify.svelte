<script>
  // Prose with "#91035"-style issue references turned into GitHub links.
  import { linkParts, issueUrl } from "../lib/fmt.js";
  let { text } = $props();
  const parts = $derived(linkParts(text));
</script>
{#each parts as p}{#if p.a}<a class="ilink" href={issueUrl(p.a)} target="_blank" rel="noopener noreferrer">#{p.a}</a>{:else}{p.t}{/if}{/each}
