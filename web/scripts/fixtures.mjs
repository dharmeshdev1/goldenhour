// Serves the development fixtures through public/api/v1, and keeps them out of every build.
//
//   node scripts/fixtures.mjs use    copy fixtures/api/v1 into public/api/v1 (run by `pnpm dev`)
//   node scripts/fixtures.mjs clean  remove public/api if the fixtures put it there (run by `pnpm build`)
//   node scripts/fixtures.mjs check  fail if fixtures reached the export in out/ (run after `next build`)
//
// A marker file, public/api/.fixtures, records that public/api holds fixtures. Data the real
// pipeline placed in public/api has no marker and is never touched or overwritten.
//
// `use` moves the freshness timestamps (generated_at, data_through, tick.at) to the current time,
// so the development site reads as live. Set GOLDENHOUR_FIXTURES_REBASE=0 to keep them as written.
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const fixtures = join(root, "fixtures", "api");
const publicApi = join(root, "public", "api");
const marker = join(publicApi, ".fixtures");
const outApi = join(root, "out", "api");

const command = process.argv[2];

function rebase(dir) {
  const read = (name) => JSON.parse(readFileSync(join(dir, name), "utf8"));
  const save = (name, doc) => writeFileSync(join(dir, name), `${JSON.stringify(doc, null, 1)}\n`);
  const meta = read("meta.json");
  const shift = Date.now() - 60_000 - Date.parse(meta.generated_at);
  if (!Number.isFinite(shift) || shift <= 0) return;
  const move = (iso) => new Date(Date.parse(iso) + shift).toISOString().replace(/\.\d{3}Z$/, "Z");
  meta.generated_at = move(meta.generated_at);
  meta.tick.at = move(meta.tick.at);
  save("meta.json", meta);
  const summary = read("summary.json");
  summary.generated_at = move(summary.generated_at);
  summary.data_through = move(summary.data_through);
  save("summary.json", summary);
  const feed = read("feed.json");
  feed.generated_at = move(feed.generated_at);
  save("feed.json", feed);
}

if (command === "use") {
  if (existsSync(publicApi) && !existsSync(marker)) {
    console.log("public/api holds data that did not come from the fixtures; leaving it as is.");
    process.exit(0);
  }
  rmSync(publicApi, { recursive: true, force: true });
  cpSync(fixtures, publicApi, { recursive: true });
  writeFileSync(marker, "Development fixtures copied by scripts/fixtures.mjs. Never deploy this directory.\n");
  if (process.env.GOLDENHOUR_FIXTURES_REBASE !== "0") rebase(join(publicApi, "v1"));
  console.log("serving development fixtures from public/api/v1");
} else if (command === "clean") {
  if (existsSync(marker)) {
    rmSync(publicApi, { recursive: true, force: true });
    console.log("removed development fixtures from public/api");
  }
} else if (command === "check") {
  if (existsSync(join(outApi, ".fixtures"))) {
    console.error("Development fixtures reached out/api. Run `node scripts/fixtures.mjs clean` and build again.");
    process.exit(1);
  }
} else {
  console.error("usage: node scripts/fixtures.mjs use|clean|check");
  process.exit(2);
}
