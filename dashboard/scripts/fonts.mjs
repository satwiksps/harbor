import { access, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

// Download from the publisher for this installation. Do not redistribute the
// Switzer binary in the source repository. See public/fonts/Switzer-LICENSE.txt.
const destination = fileURLToPath(
  new URL("../public/fonts/Switzer-Variable.woff2", import.meta.url),
);
try {
  await access(destination);
  process.exit(0);
} catch {
  /* First installation. */
}
const cssResponse = await fetch(
  "https://api.fontshare.com/v2/css?f[]=switzer@variable&display=swap",
  { signal: AbortSignal.timeout(30000) },
);
if (!cssResponse.ok)
  throw new Error(`Fontshare CSS request failed (${cssResponse.status}).`);
const css = await cssResponse.text();
const block = [...css.matchAll(/@font-face\s*\{([^}]+)\}/g)].find(
  (match) =>
    /font-weight:\s*100 900/.test(match[1]) &&
    /font-style:\s*normal/.test(match[1]),
);
const source = block?.[1].match(/url\(['"]?([^)'"\s]+\.woff2)['"]?\)/)?.[1];
if (!source)
  throw new Error("The publisher did not return the Switzer variable webfont.");
const url = new URL(source, "https://api.fontshare.com");
if (url.protocol !== "https:" || url.hostname !== "cdn.fontshare.com")
  throw new Error("Unexpected font publisher URL.");
const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
if (!response.ok) throw new Error(`Font download failed (${response.status}).`);
const bytes = new Uint8Array(await response.arrayBuffer());
if (
  bytes.length > 500000 ||
  String.fromCharCode(...bytes.slice(0, 4)) !== "wOF2"
)
  throw new Error("Invalid webfont download.");
await mkdir(fileURLToPath(new URL("../public/fonts/", import.meta.url)), {
  recursive: true,
});
await writeFile(destination, bytes);
console.log(
  "Switzer downloaded directly from Fontshare for this installation.",
);
