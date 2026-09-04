// Builds the page's client (web-src/) into ONE self-contained IIFE, web-src/dist/app.js,
// which tools/build.py inlines into web/template.html. No runtime dependency ships: the
// Svelte runtime is bundled, the deploy box stays Node-less, archives stay single files.
// (.mjs: the browser suites are CommonJS, so package.json can't be "type": "module".)
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte({ compilerOptions: { runes: true, css: "external" } })],
  define: { "process.env.NODE_ENV": '"production"' },
  build: {
    lib: { entry: "web-src/main.js", name: "ClawStat", formats: ["iife"], fileName: () => "app.js" },
    outDir: "web-src/dist",
    emptyOutDir: true,
    minify: true,
    sourcemap: false,
    target: "es2020",
    cssCodeSplit: false,
    reportCompressedSize: false,
  },
});
