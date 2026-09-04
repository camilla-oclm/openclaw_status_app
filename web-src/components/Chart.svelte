<script module>
  let seq = 0;   // unique gradient ids across the four charts
</script>
<script>
  // One Trends chart from a spec (lib/charts.js): grid, layers, the cap annotation, and the
  // hover tip — a guide line snaps to the nearest run and the tip lists the date, each
  // series' value and the version assessed. Pointer-only sugar: the chart stays aria-hidden
  // and its card title is the accessible name.
  import { px, py } from "../lib/charts.js";
  import { fmtDate } from "../lib/fmt.js";
  let { spec, title, sub } = $props();
  const uid = ++seq;
  const n = $derived(spec.rows.length);
  let hi = $state(-1);
  let svgEl;
  function move(ev) {
    if (n < 2) return;
    const box = svgEl.getBoundingClientRect(); if (!box.width) return;
    const sx = (ev.clientX - box.left) / box.width * spec.c.W;
    const i = Math.round((sx - spec.c.l) / (spec.c.r - spec.c.l) * (n - 1));
    hi = Math.max(0, Math.min(n - 1, i));
  }
  const hx = $derived(hi >= 0 ? px(spec.c, hi, n) : 0);
  const fx = $derived(hi >= 0 ? (hx - spec.c.l) / (spec.c.r - spec.c.l) : 0);
</script>
<div><div class="chartwrap" role="img" aria-label="{title} chart — {sub}" onpointermove={move} onpointerleave={() => { hi = -1; }}><svg class="tchart2" viewBox="0 0 {spec.c.W} {spec.c.H}" aria-hidden="true" bind:this={svgEl}>{#if spec.grid.length}<g class="tgrid">{#each spec.grid as g}<line x1={spec.c.l} x2={spec.c.r} y1={g.y} y2={g.y}></line><text class="taxis2" x={spec.c.l - 4} y={g.y + 3} text-anchor="end">{g.label}</text>{/each}</g>{/if}{#each spec.layers as L, i}{#if L.k === "area"}<defs><linearGradient id="tg{uid}-{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:{L.stroke};stop-opacity:.34"></stop><stop offset="1" style="stop-color:{L.stroke};stop-opacity:0"></stop></linearGradient></defs><path d={L.d} fill="url(#tg{uid}-{i})" stroke="none"></path>{:else if L.k === "path"}<path d={L.d} fill="none" stroke={L.stroke} stroke-width={L.wd} stroke-linejoin="round" stroke-linecap="round" stroke-dasharray={L.dash}></path>{:else if L.k === "end"}<circle cx={L.cx} cy={L.cy} r="3.2" fill={L.fill} class="tend"></circle>{:else if L.k === "poly"}<path d={L.d} fill={L.fill} opacity="0.55" stroke="none"></path>{:else if L.k === "rect"}<rect x={L.x} y={L.y} width={L.w} height={L.h} fill={L.fill} opacity="0.10"></rect>{:else if L.k === "text"}<text class="taxis2" x={L.x} y={L.y} text-anchor="end" fill={L.fill}>{L.txt}</text>{:else if L.k === "steps"}<polyline points={L.points} fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></polyline>{:else if L.k === "vline"}<line x1={L.x} x2={L.x} y1={spec.c.t} y2={spec.c.b} stroke="var(--accent2)" stroke-width="1" stroke-dasharray="2 3" opacity="0.7"></line>{/if}{/each}{#if spec.cap}<line x1={spec.c.l} x2={spec.c.r} y1={spec.cap.y} y2={spec.cap.y} stroke="var(--faint)" stroke-width="1" stroke-dasharray="5 4" opacity="0.9"></line><rect class="tcap-bg" x={spec.cap.x} y={spec.cap.y - 12.5} width={spec.cap.w} height="12" rx="3"></rect><text class="taxis2 tcap" x={spec.cap.tx} y={spec.cap.y - 4}>{spec.cap.txt}</text>{/if}{#if n >= 2}<line class="tguide{hi >= 0 ? ' on' : ''}" x1={hx} x2={hx} y1={spec.c.t} y2={spec.c.b}></line>{#each spec.series as s}<circle r="3.5" fill={s.color} class="tdot{hi >= 0 ? ' on' : ''}" cx={hx} cy={hi >= 0 ? py(spec.c, s.values[hi], s.vmax) : 0}></circle>{/each}{/if}</svg>{#if n >= 2}<div class="ttip{fx > 0.6 ? ' flip' : ''}" hidden={hi < 0} style="left:{(fx * 100).toFixed(1)}%">{#if hi >= 0}<div class="tt-h">{fmtDate(spec.rows[hi].t) + (spec.rows[hi].version ? " · v" + spec.rows[hi].version : "")}</div>{#each spec.series as s}<div class="tt-r"><span class="tc-sw" style="background:{s.color}"></span>{s.name}<b>{s.fmt ? s.fmt(s.values[hi], hi) : String(s.values[hi])}</b></div>{/each}{/if}</div>{/if}</div><div class="tc-legend">{#each spec.legend as it}<span class="tc-k"><span class="tc-sw" style="background:{it[1]}"></span>{it[0]}</span>{/each}</div></div>
