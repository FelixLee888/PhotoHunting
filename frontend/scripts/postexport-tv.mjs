import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendRoot = dirname(scriptDir);
const outDir = join(frontendRoot, "out");

await mkdir(join(outDir, "tv"), { recursive: true });
await copyFile(join(outDir, "tv.html"), join(outDir, "tv", "index.html"));

try {
  await copyFile(join(outDir, "tv.txt"), join(outDir, "tv", "index.txt"));
} catch {
  // Text export is optional.
}
