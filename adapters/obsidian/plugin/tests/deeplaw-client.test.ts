import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  DeepLawClient,
  assertWritableRelativePath,
  canonicalVaultRelativePath,
  canonicalWikiPath,
  extractFragmentIds,
  parseCompilationRunPickerItems,
  parseSourcePickerItems,
  resolveInsideVault,
  sanitizeProviderValue,
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

test("compilation status reads the semantic v3 status surface", async () => {
  let capturedArgs: readonly string[] = [];
  const factory: ProcessFactory = (_executable, args) => {
    capturedArgs = args;
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const child = new EventEmitter() as ReturnType<ProcessFactory>;
    Object.assign(child, { stdout, stderr, stdin: new PassThrough(), kill: () => true });
    queueMicrotask(() => {
      stdout.end(JSON.stringify({ schema_version: "deeplaw.source-compilation-run/v3" }));
      stderr.end();
      child.emit("close", 0);
    });
    return child;
  };
  const client = new DeepLawClient("deeplaw", "/tmp/vault", factory);
  await client.compilationStatus("compilationrun_0123456789abcdef0123456789abcdef");
  assert.deepEqual(capturedArgs.slice(3, 5), ["semantic", "status"]);
});

test("v0.13 client binds Query v6, Profile v3, and the host-neutral envelope", async () => {
  const calls: readonly string[][] = [];
  const factory: ProcessFactory = (_executable, args) => {
    (calls as string[][]).push([...args]);
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const child = new EventEmitter() as ReturnType<ProcessFactory>;
    Object.assign(child, { stdout, stderr, stdin: new PassThrough(), kill: () => true });
    queueMicrotask(() => {
      const output = args.includes("agent-context")
        ? {
            schema_version: "deeplaw.agent-context-envelope/v1",
            task: "bounded task", goal: null, workspace_identity: "workspace", repository_identity: "repository",
            commit: null, branch: null, active_files: [], selected_text: null, open_tabs: [], current_note: null,
            tool_result_digests: [], requested_purpose: "answer", scope: "project", max_sensitivity: "private",
            policy: {}, budget: {}, ephemeral: true, persistence_allowed: false, persistence_performed: false,
            authority: "none", legal_authority: false, envelope_sha256: "0".repeat(64)
          }
        : {
            schema_version: "deeplaw.purpose-aware-retrieval/v3",
            query_plan: { schema_version: "deeplaw.knowledge-query-plan/v6" },
            statements: [], evidence: [], contradictions: [], gaps: [], capsule: {}
          };
      stdout.end(JSON.stringify(output));
      stderr.end();
      child.emit("close", 0);
    });
    return child;
  };
  const client = new DeepLawClient("deeplaw", "/tmp/vault", factory);
  await client.query("bounded task", "answer", "compact");
  await client.agentContext({
    task: "bounded task",
    workspaceIdentity: "obsidian-workspace-vault",
    repositoryIdentity: "obsidian-repository-vault",
    activeFiles: ["notes/active.md"],
    openTabs: ["notes/active.md"],
    currentNote: "notes/active.md",
    selectedText: "line one\n\tline two"
  });
  await client.beginCompilation("sourcerev_0123456789abcdef0123456789abcdef", "grant_0123456789abcdef0123456789abcdef");
  assert.equal(calls[0]?.includes("--query-plan-version") && calls[0]?.includes("6"), true);
  assert.equal(calls[0]?.includes("--capsule-projection") && calls[0]?.includes("compact"), true);
  assert.equal(calls[1]?.[0], "knowledge");
  assert.equal(calls[1]?.includes("agent-context"), true);
  assert.equal(calls[1]?.includes("--workspace-identity"), true);
  assert.equal(calls[1]?.includes("--repository-identity"), true);
  assert.equal(calls[2]?.includes("--compiler-profile-version") && calls[2]?.includes("3"), true);
});

test("picker parsers and evidence extraction fail closed without manual IDs", () => {
  const sources = parseSourcePickerItems({
    sources: [{
      source_revision_id: "sourcerev_0123456789abcdef0123456789abcdef",
      title: "Policy source",
      logical_path: "sources/policy.md",
      status: "active",
      trust: "user_provided",
      sensitivity: "private",
      warnings: []
    }]
  });
  assert.equal(sources[0]?.logicalPath, "sources/policy.md");
  assert.throws(() => parseSourcePickerItems({ sources: [{ title: "missing stable identity" }] }));
  const runs = parseCompilationRunPickerItems({ runs: [{
    compilation_run_id: "compilationrun_0123456789abcdef0123456789abcdef",
    source_revision_id: "sourcerev_0123456789abcdef0123456789abcdef",
    compiler_profile: "living-wiki-agent",
    compiler_profile_version: "3",
    status: "succeeded",
    packet_count: 1,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z"
  }] });
  assert.equal(runs[0]?.status, "succeeded");
  assert.deepEqual(extractFragmentIds({ statements: [{ source_refs: [{ fragment_id: "irfragment_a" }] }], evidence: [{ fragment_revision_id: "fragment-revision_b" }] }), ["irfragment_a", "fragment-revision_b"]);
  assert.equal(canonicalWikiPath("wiki/Policy.md"), "wiki/Policy.md");
  assert.throws(() => canonicalWikiPath("notes/Policy.md"));
});

test("provider receipts redact local paths and secret-shaped fields", () => {
  assert.deepEqual(sanitizeProviderValue({ path: "/private/operator/secret.md", api_key: "sk-local" }), {
    path: "[local path redacted]",
    api_key: "[redacted]"
  });
  assert.equal(sanitizeProviderValue("evidence /Users/operator/note.md"), "evidence [local path redacted]");
});
