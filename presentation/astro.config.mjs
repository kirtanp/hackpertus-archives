import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

// SINGLE_FILE=1 builds into ./dist-single so the post-build script
// (scripts/single-file.mjs) can emit one self-contained HTML per deck.
const singleFileBuild = process.env.SINGLE_FILE === "1";

export default defineConfig({
  output: "static",
  outDir: singleFileBuild ? "./dist-single" : "./dist",
  vite: {
    plugins: [tailwindcss()],
  },
});
