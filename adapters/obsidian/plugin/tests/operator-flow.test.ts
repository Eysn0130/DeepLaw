import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("v0.13 operator surface uses pickers and bounded read-only views", async () => {
  const source = await readFile(new URL("../main.ts", import.meta.url), "utf8");
  assert.match(source, /SuggestModal/);
  assert.match(source, /parseSourcePickerItems/);
  assert.match(source, /parseCompilationRunPickerItems/);
  assert.match(source, /extractFragmentIds/);
  assert.match(source, /display-duty-coverage/);
  assert.match(source, /display-statement-evidence/);
  assert.match(source, /wiki-backlinks/);
  assert.match(source, /wiki-outlinks/);
  assert.match(source, /Load next page/);
  assert.doesNotMatch(source, /PromptModal|\.prompt\(/);
  assert.doesNotMatch(source, /sourceRevisionId\s*=\s*await/);
  assert.doesNotMatch(source, /compilationRunId\s*=\s*await/);
});

test("operator flow keeps Wiki selection exact before opening a file", async () => {
  const source = await readFile(new URL("../main.ts", import.meta.url), "utf8");
  const pageCall = source.indexOf("this.client().wikiPage(selected.file.path)");
  const exactLookup = source.indexOf("this.app.vault.getFileByPath(canonical)");
  assert.ok(pageCall >= 0 && exactLookup > pageCall);
  assert.match(source, /total_count: \$\{latest\.totalCount\} · cursor:/);
  assert.match(source, /capsule: queryValue\.capsule/);
  const duty = source.indexOf('command("display-duty-coverage"');
  assert.ok(duty >= 0 && source.indexOf("pickCompilationRun", duty) > duty);
});
