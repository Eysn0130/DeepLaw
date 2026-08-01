import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  DeepLawClient,
  assertWritableRelativePath,
  canonicalVaultRelativePath,
  resolveInsideVault,
  type ProcessFactory
} from "../deeplaw-client";

test("path policy rejects traversal and canonical roots", () => {
  assert.equal(assertWritableRelativePath("sources/inbox/a.md"), "sources/inbox/a.md");
  assert.equal(assertWritableRelativePath("drafts/a.md"), "drafts/a.md");
  assert.throws(() => canonicalVaultRelativePath("../private.md"));
  assert.throws(() => assertWritableRelativePath("knowledge/a.md"));
  assert.throws(() => assertWritableRelativePath("wiki/a.md"));
  assert.equal(resolveInsideVault("/tmp/vault", "notes/a.md"), "/tmp/vault/notes/a.md");
});

test("client uses argv arrays, shell false, bounded JSON, and no token environment", async () => {
  let captured: { executable: string; args: readonly string[]; shell: false; env: NodeJS.ProcessEnv } | undefined;
  const factory: ProcessFactory = (executable, args, options) => {
    captured = { executable, args, shell: options.shell, env: options.env };
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const child = new EventEmitter() as ReturnType<ProcessFactory>;
    Object.assign(child, {
      stdout,
      stderr,
      stdin: new PassThrough(),
      kill: () => true
    });
    queueMicrotask(() => {
      stdout.end('{"valid":true}');
      stderr.end();
      child.emit("close", 0);
    });
    return child;
  };
  const result = await new DeepLawClient("deeplaw", "/tmp/vault", factory).verify();
  assert.equal(result.value.valid, true);
  assert.equal(captured?.executable, "deeplaw");
  assert.equal(captured?.shell, false);
  assert.deepEqual(captured?.args.slice(0, 3), ["knowledge", "--format", "json"]);
  assert.equal(Object.keys(captured?.env ?? {}).some((key) => /token|secret|key/i.test(key)), false);
});

test("client rejects executable text containing arguments", () => {
  assert.throws(() => new DeepLawClient("deeplaw --unsafe", "/tmp/vault"));
});

test("refresh always binds the owner-created compiler grant", async () => {
  let capturedArgs: readonly string[] = [];
  const factory: ProcessFactory = (_executable, args) => {
    capturedArgs = args;
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const child = new EventEmitter() as ReturnType<ProcessFactory>;
    Object.assign(child, {
      stdout,
      stderr,
      stdin: new PassThrough(),
      kill: () => true
    });
    queueMicrotask(() => {
      stdout.end('{"valid":true}');
      stderr.end();
      child.emit("close", 0);
    });
    return child;
  };
  await new DeepLawClient("deeplaw", "/tmp/vault", factory).refresh(
    "sr_0123456789abcdef0123456789abcdef",
    "grant_0123456789abcdef0123456789abcdef"
  );
  assert.deepEqual(capturedArgs.slice(0, 3), ["knowledge", "--format", "json"]);
  assert.equal(capturedArgs.includes("--grant-id"), true);
  assert.equal(capturedArgs.includes("grant_0123456789abcdef0123456789abcdef"), true);
});
