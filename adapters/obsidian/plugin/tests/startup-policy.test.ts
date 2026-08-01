import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("startup defers Vault create observation until layout ready", async () => {
  const source = await readFile(new URL("../main.ts", import.meta.url), "utf8");
  const ready = source.indexOf("workspace.onLayoutReady");
  const create = source.indexOf('vault.on("create"');
  assert.ok(ready >= 0 && create > ready);
  const onloadBody = source.slice(source.indexOf("async onload"), source.indexOf("async saveSettings"));
  assert.equal(onloadBody.includes("ingestSource("), false);
});

test("plugin never enables shell strings or canonical writes", async () => {
  const client = await readFile(new URL("../deeplaw-client.ts", import.meta.url), "utf8");
  assert.match(client, /shell:\s*false/);
  assert.doesNotMatch(client, /shell:\s*true/);
  assert.match(client, /READONLY_ROOTS/);
  assert.doesNotMatch(client, /writeFile|appendFile|rename\(/);
});
