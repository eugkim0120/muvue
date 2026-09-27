import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const here = fileURLToPath(new URL(".", import.meta.url));
// The daemon serves this file on every GET /, so a build (or a watch
// rebuild) is live as soon as the copy lands.
const STATIC_INDEX = resolve(here, "../src/muvue/api/static/index.html");

function copyToStatic() {
  return {
    name: "muvue-copy-to-static",
    closeBundle() {
      copyFileSync(resolve(here, "dist/index.html"), STATIC_INDEX);
    },
  };
}

export default defineConfig({
  plugins: [preact(), viteSingleFile(), copyToStatic()],
  build: { minify: false, target: "es2020", outDir: "dist", emptyOutDir: true, cssCodeSplit: false },
  test: { environment: "jsdom", globals: true, setupFiles: ["./test/setup.ts"], include: ["test/**/*.test.{ts,tsx}"] },
});
