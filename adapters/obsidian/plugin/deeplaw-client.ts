import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { isAbsolute, normalize, relative, resolve, sep } from "node:path";

export const WRITABLE_ROOTS = ["drafts", "notes", "sources/inbox"] as const;
export const READONLY_ROOTS = [".deeplaw", "canvas", "knowledge", "memory", "sources", "wiki"] as const;
const MAX_STDOUT_BYTES = 65_536;
const MAX_STDERR_BYTES = 16_384;
const MAX_TASK_CHARACTERS = 5_000;
const MAX_SELECTED_TEXT_CHARACTERS = 12_000;
const MAX_IDENTITY_CHARACTERS = 500;
const MAX_WIKI_PATH_CHARACTERS = 500;
const MAX_CURSOR_CHARACTERS = 256;

export type ContextPurpose =
  | "answer"
  | "verify"
  | "quote"
  | "historical"
  | "legal"
  | "debug"
  | "freshness_check";
export type ContextScope = "personal" | "project" | "domain";
export type ContextSensitivity = "public" | "internal" | "private" | "restricted";

export interface SourcePickerItem {
  readonly sourceRevisionId: string;
  readonly title: string;
  readonly logicalPath: string;
  readonly status: string;
  readonly trust: string;
  readonly sensitivity: string;
  readonly warnings: readonly string[];
}

export interface CompilationRunPickerItem {
  readonly compilationRunId: string;
  readonly sourceRevisionId: string;
  readonly compilerProfile: string;
  readonly compilerProfileVersion: string;
  readonly status: string;
  readonly packetCount: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface AgentContextOptions {
  readonly task: string;
  readonly workspaceIdentity: string;
  readonly repositoryIdentity: string;
  readonly activeFiles?: readonly string[];
  readonly openTabs?: readonly string[];
  readonly currentNote?: string;
  readonly selectedText?: string;
  readonly purpose?: ContextPurpose;
  readonly scope?: ContextScope;
  readonly maxSensitivity?: ContextSensitivity;
  readonly maxTokens?: number;
}

export interface QueryOptions {
  readonly scope?: "personal" | "project" | "domain";
  readonly maxSensitivity?: "public" | "internal" | "private";
}

function requireBoundedString(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\0\u0000-\u001f\u007f]/.test(value)) {
    throw new Error(`${label} is invalid or exceeds its bound`);
  }
  return value;
}

function requireBoundedText(value: unknown, label: string, maximum: number): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > maximum ||
    /[\0\u0001-\u0008\u000b\u000c\u000e-\u001f\u007f\r]/.test(value)
  ) {
    throw new Error(`${label} is invalid or exceeds its bound`);
  }
  return value;
}

function requireStableId(value: unknown, label: string): string {
  const candidate = requireBoundedString(value, label, 200);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(candidate)) throw new Error(`${label} is not a stable DeepLaw identity`);
  return candidate;
}

function requireEnum<T extends string>(value: unknown, label: string, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new Error(`${label} is invalid`);
  return value as T;
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} is not a JSON object`);
  }
  return value as Record<string, unknown>;
}

function requireField(record: Record<string, unknown>, key: string, label: string): unknown {
  if (!(key in record)) throw new Error(`${label} is missing ${key}`);
  return record[key];
}

function optionalStringField(record: Record<string, unknown>, key: string, label: string, maximum = 500): string {
  const value = record[key];
  if (value === undefined || value === null) return "";
  return requireBoundedString(value, `${label}.${key}`, maximum);
}

function requiredStringField(record: Record<string, unknown>, key: string, label: string, maximum = 500): string {
  return requireBoundedString(requireField(record, key, label), `${label}.${key}`, maximum);
}

function warningsField(record: Record<string, unknown>, label: string): readonly string[] {
  const value = record.warnings;
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 64) throw new Error(`${label}.warnings is invalid`);
  return value.map((item, index) => requireBoundedString(item, `${label}.warnings[${index}]`, 2_000));
}

function boundedArray(value: unknown, label: string, maximum: number): readonly unknown[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error(`${label} is invalid or exceeds its bound`);
  return value;
}

/** Parse the closed Source Picker response; stable IDs never come from user text. */
export function parseSourcePickerItems(value: unknown): readonly SourcePickerItem[] {
  const root = asRecord(value, "Source list response");
  const sources = boundedArray(requireField(root, "sources", "Source list response"), "Source list sources", 1_000);
  return sources.map((candidate, index) => {
    const record = asRecord(candidate, `Source list item ${index}`);
    return {
      sourceRevisionId: requireStableId(requireField(record, "source_revision_id", `Source list item ${index}`), `Source list item ${index}.source_revision_id`),
      title: optionalStringField(record, "title", `Source list item ${index}`, 500),
      logicalPath: optionalStringField(record, "logical_path", `Source list item ${index}`, 500),
      status: optionalStringField(record, "status", `Source list item ${index}`, 100),
      trust: optionalStringField(record, "trust", `Source list item ${index}`, 100),
      sensitivity: optionalStringField(record, "sensitivity", `Source list item ${index}`, 100),
      warnings: warningsField(record, `Source list item ${index}`)
    };
  });
}

/** Parse the closed Source Compilation status response for the Run Picker. */
export function parseCompilationRunPickerItems(value: unknown): readonly CompilationRunPickerItem[] {
  const root = asRecord(value, "Compilation list response");
  const runs = boundedArray(requireField(root, "runs", "Compilation list response"), "Compilation list runs", 1_000);
  return runs.map((candidate, index) => {
    const record = asRecord(candidate, `Compilation run item ${index}`);
    const packetCount = requireField(record, "packet_count", `Compilation run item ${index}`);
    if (typeof packetCount !== "number" || !Number.isSafeInteger(packetCount) || packetCount < 1 || packetCount > 10_000) {
      throw new Error(`Compilation run item ${index}.packet_count is invalid`);
    }
    return {
      compilationRunId: requireStableId(requireField(record, "compilation_run_id", `Compilation run item ${index}`), `Compilation run item ${index}.compilation_run_id`),
      sourceRevisionId: requireStableId(requireField(record, "source_revision_id", `Compilation run item ${index}`), `Compilation run item ${index}.source_revision_id`),
      compilerProfile: requiredStringField(record, "compiler_profile", `Compilation run item ${index}`, 200),
      compilerProfileVersion: requiredStringField(record, "compiler_profile_version", `Compilation run item ${index}`, 100),
      status: requiredStringField(record, "status", `Compilation run item ${index}`, 100),
      packetCount,
      createdAt: requiredStringField(record, "created_at", `Compilation run item ${index}`, 100),
      updatedAt: requiredStringField(record, "updated_at", `Compilation run item ${index}`, 100)
    };
  });
}

/** Validate the exact host-neutral, ephemeral envelope before it is shown to a provider. */
export function parseAgentContextEnvelope(value: unknown): Record<string, unknown> {
  const root = asRecord(value, "Agent Context Envelope");
  const required = [
    "schema_version", "task", "goal", "workspace_identity", "repository_identity", "commit",
    "branch", "active_files", "selected_text", "open_tabs", "current_note", "tool_result_digests",
    "requested_purpose", "scope", "max_sensitivity", "policy", "budget", "ephemeral",
    "persistence_allowed", "persistence_performed", "authority", "legal_authority", "envelope_sha256"
  ];
  for (const key of required) if (!(key in root)) throw new Error(`Agent Context Envelope is missing ${key}`);
  for (const key of Object.keys(root)) if (!required.includes(key)) throw new Error(`Agent Context Envelope contains unsupported field ${key}`);
  if (root.schema_version !== "deeplaw.agent-context-envelope/v1") throw new Error("Agent Context Envelope schema is unsupported");
  if (root.ephemeral !== true || root.persistence_allowed !== false || root.persistence_performed !== false) {
    throw new Error("Agent Context Envelope persistence policy is invalid");
  }
  if (root.authority !== "none" || root.legal_authority !== false) {
    throw new Error("Agent Context Envelope authority boundary is invalid");
  }
  if (!Array.isArray(root.active_files) || root.active_files.length > 64 || !Array.isArray(root.open_tabs) || root.open_tabs.length > 32) {
    throw new Error("Agent Context Envelope path collections are invalid");
  }
  return root;
}

/** Validate the Query Plan v6 result required by the context/evidence views. */
export function parseQueryV6Response(value: unknown): Record<string, unknown> {
  const root = asRecord(value, "Query v6 response");
  if (root.schema_version !== "deeplaw.purpose-aware-retrieval/v3") throw new Error("Query response is not purpose-aware v3");
  const plan = asRecord(root.query_plan, "Query v6 query_plan");
  if (plan.schema_version !== "deeplaw.knowledge-query-plan/v6") throw new Error("Query response is not Query Plan v6");
  if (!Array.isArray(root.statements) || root.statements.length > 20) throw new Error("Query v6 statements exceed their bound");
  if (!Array.isArray(root.evidence) || root.evidence.length > 32) throw new Error("Query v6 evidence exceeds its bound");
  if (!Array.isArray(root.contradictions) || root.contradictions.length > 32) throw new Error("Query v6 contradictions exceed their bound");
  if (!Array.isArray(root.gaps) || root.gaps.length > 32) throw new Error("Query v6 gaps exceed their bound");
  asRecord(root.capsule, "Query v6 capsule");
  return root;
}

/** Recursively collect only fragment identities present in Query v6 evidence. */
export function extractFragmentIds(value: unknown): readonly string[] {
  const found = new Set<string>();
  const visit = (candidate: unknown, depth: number): void => {
    if (depth > 32 || candidate === null || candidate === undefined) return;
    if (Array.isArray(candidate)) {
      if (candidate.length > 2_000) throw new Error("Query evidence exceeds the fragment picker bound");
      for (const item of candidate) visit(item, depth + 1);
      return;
    }
    if (typeof candidate !== "object") return;
    const record = candidate as Record<string, unknown>;
    for (const [key, item] of Object.entries(record)) {
      if (key === "fragment_id" || key === "fragment_revision_id") {
        const fragmentId = requireStableId(item, `Query evidence ${key}`);
        found.add(fragmentId);
      }
      visit(item, depth + 1);
    }
  };
  visit(value, 0);
  return [...found];
}

export function canonicalWikiPath(value: string): string {
  const canonical = canonicalVaultRelativePath(value);
  if (canonical.length > MAX_WIKI_PATH_CHARACTERS || !canonical.startsWith("wiki/") || !canonical.endsWith(".md")) {
    throw new Error("DeepLaw Wiki path must be an exact wiki/*.md path");
  }
  return canonical;
}

export function canonicalVaultRelativePath(value: string): string {
  if (!value || value.includes("\0") || value.includes("\\") || isAbsolute(value)) {
    throw new Error("DeepLaw path must be a bounded Vault-relative path");
  }
  const canonical = normalize(value).split(sep).join("/").replace(/^\.\//, "");
  if (canonical === ".." || canonical.startsWith("../") || canonical.includes("/../")) {
    throw new Error("DeepLaw path escapes the Vault boundary");
  }
  return canonical;
}

export function assertWritableRelativePath(value: string): string {
  const canonical = canonicalVaultRelativePath(value);
  if (!WRITABLE_ROOTS.some((root) => canonical === root || canonical.startsWith(`${root}/`))) {
    throw new Error("DeepLaw canonical and derived roots are read-only");
  }
  return canonical;
}

export function resolveInsideVault(vaultRoot: string, relativePath: string): string {
  if (!isAbsolute(vaultRoot)) throw new Error("DeepLaw Vault path must be absolute");
  const canonical = canonicalVaultRelativePath(relativePath);
  const target = resolve(vaultRoot, canonical);
  const back = relative(resolve(vaultRoot), target);
  if (!back || back === ".." || back.startsWith(`..${sep}`) || isAbsolute(back)) {
    throw new Error("DeepLaw path escapes the configured Vault");
  }
  return target;
}

export interface CommandResult {
  readonly value: Record<string, unknown>;
  readonly stdoutBytes: number;
  readonly stderrBytes: number;
}

export function sanitizeProviderValue(value: unknown): unknown {
  if (typeof value === "string") {
    if (isAbsolute(value) || /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("\\\\")) {
      return "[local path redacted]";
    }
    return value.replace(
      /(^|[\s"'([{])((?:\/|[A-Za-z]:[\\/]|\\\\)[^\s"'<>)]*)/g,
      "$1[local path redacted]"
    );
  }
  if (Array.isArray(value)) return value.map(sanitizeProviderValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => {
      if (/token|secret|private.?key|api.?key|access.?key|password|credential/i.test(key)) return [key, "[redacted]"];
      return [key, sanitizeProviderValue(item)];
    }));
  }
  return value;
}

export type ProcessFactory = (
  executable: string,
  args: readonly string[],
  options: { cwd: string; shell: false; env: NodeJS.ProcessEnv }
) => ChildProcessWithoutNullStreams;

export class DeepLawClient {
  constructor(
    private readonly executable: string,
    private readonly vaultRoot: string,
    private readonly processFactory: ProcessFactory = spawn
  ) {
    if (!executable || /\s/.test(executable) || executable.includes("\0")) {
      throw new Error("DeepLaw executable must not contain arguments");
    }
    if (!isAbsolute(vaultRoot)) throw new Error("DeepLaw Vault path must be absolute");
  }

  async invoke(args: readonly string[]): Promise<CommandResult> {
    if (args.some((item) => item.includes("\0"))) throw new Error("DeepLaw argument is invalid");
    const environment: NodeJS.ProcessEnv = {
      PATH: process.env.PATH,
      LANG: process.env.LANG ?? "C.UTF-8",
      LC_ALL: process.env.LC_ALL ?? "C.UTF-8",
      NO_COLOR: "1"
    };
    const child = this.processFactory(this.executable, args, {
      cwd: this.vaultRoot,
      shell: false,
      env: environment
    });
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    const timeout = setTimeout(() => child.kill(), 120_000);
    child.stdout.on("data", (chunk: Buffer) => {
      stdout = Buffer.concat([stdout, chunk]);
      if (stdout.length > MAX_STDOUT_BYTES) child.kill();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = Buffer.concat([stderr, chunk]);
      if (stderr.length > MAX_STDERR_BYTES) child.kill();
    });
    const exitCode = await new Promise<number>((resolveExit, reject) => {
      child.once("error", reject);
      child.once("close", (code) => resolveExit(code ?? 1));
    }).finally(() => clearTimeout(timeout));
    if (stdout.length > MAX_STDOUT_BYTES || stderr.length > MAX_STDERR_BYTES) {
      throw new Error("DeepLaw process output exceeded its hard byte limit");
    }
    if (exitCode !== 0) throw new Error("DeepLaw command failed closed");
    let value: unknown;
    try {
      value = JSON.parse(stdout.toString("utf8"));
    } catch {
      throw new Error("DeepLaw command did not return bounded JSON");
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("DeepLaw command returned an invalid receipt");
    }
    return { value: value as Record<string, unknown>, stdoutBytes: stdout.length, stderrBytes: stderr.length };
  }

  private knowledge(...args: string[]): Promise<CommandResult> {
    return this.invoke(["knowledge", "--format", "json", ...args]);
  }

  verify(): Promise<CommandResult> {
    return this.knowledge("autonomy", "verify", "--vault", this.vaultRoot);
  }

  ingestSource(sourcePath: string): Promise<CommandResult> {
    if (!isAbsolute(sourcePath)) throw new Error("Source registration requires an absolute file path");
    return this.knowledge("ingest", "--vault", this.vaultRoot, "--source", sourcePath,
      "--source-kind", "document", "--confirm-no-case-data");
  }

  sourceList(): Promise<CommandResult> {
    return this.knowledge("source", "list", "--vault", this.vaultRoot);
  }

  compilationList(sourceRevisionId?: string): Promise<CommandResult> {
    const args = ["compile", "list", "--vault", this.vaultRoot];
    if (sourceRevisionId) args.push("--source-revision-id", requireStableId(sourceRevisionId, "Source Revision ID"));
    return this.knowledge(...args);
  }

  compilationStatus(runId: string): Promise<CommandResult> {
    return this.knowledge("semantic", "status", "--vault", this.vaultRoot,
      "--run-id", requireStableId(runId, "Compilation Run ID"));
  }

  beginCompilation(sourceRevisionId: string, grantId: string): Promise<CommandResult> {
    requireStableId(sourceRevisionId, "Source Revision ID");
    requireStableId(grantId, "Owner-created compiler grant");
    return this.knowledge("compile", "begin", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--source-revision-id", sourceRevisionId,
      "--compiler-profile", "living-wiki-agent", "--compiler-profile-version", "3",
      "--host-identity", "obsidian", "--confirm-no-case-data");
  }

  resumeProjection(runId: string, grantId: string): Promise<CommandResult> {
    requireStableId(runId, "Compilation Run ID");
    requireStableId(grantId, "Owner-created compiler grant");
    return this.knowledge("compile", "resume", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--run-id", runId, "--project", "--confirm-no-case-data");
  }

  refresh(
    sourceRevisionId: string,
    grantId: string,
    replacementSourceRevisionId?: string
  ): Promise<CommandResult> {
    requireStableId(sourceRevisionId, "Source Revision ID");
    requireStableId(grantId, "Owner-created compiler grant");
    const args = ["compile", "refresh", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--source-revision-id", sourceRevisionId,
      "--confirm-no-case-data"];
    if (replacementSourceRevisionId) {
      args.push(
        "--replacement-source-revision-id",
        requireStableId(replacementSourceRevisionId, "Replacement Source Revision ID")
      );
    }
    return this.knowledge(...args);
  }

  query(query: string, purpose: ContextPurpose = "answer", projection = "standard", options: QueryOptions = {}): Promise<CommandResult> {
    const task = requireBoundedText(query.trim(), "Query", MAX_TASK_CHARACTERS);
    const selectedPurpose = requireEnum(purpose, "Query purpose", ["answer", "verify", "quote", "historical", "legal", "debug", "freshness_check"] as const);
    const selectedProjection = requireEnum(projection, "Query capsule projection", ["compact", "standard", "audit"] as const);
    const args = [
      "query", "--vault", this.vaultRoot, "--query", task,
      "--purpose", selectedPurpose, "--query-plan-version", "6",
      "--capsule-projection", selectedProjection
    ];
    if (options.scope) args.push("--scope", requireEnum(options.scope, "Query scope", ["personal", "project", "domain"] as const));
    if (options.maxSensitivity) args.push("--max-sensitivity", requireEnum(options.maxSensitivity, "Query maximum sensitivity", ["public", "internal", "private"] as const));
    return this.knowledge(...args).then((result) => {
      parseQueryV6Response(result.value);
      return result;
    });
  }

  agentContext(options: AgentContextOptions): Promise<CommandResult> {
    const task = requireBoundedText(options.task.trim(), "Agent context task", MAX_TASK_CHARACTERS);
    const workspaceIdentity = requireBoundedString(options.workspaceIdentity, "Workspace identity", MAX_IDENTITY_CHARACTERS);
    const repositoryIdentity = requireBoundedString(options.repositoryIdentity, "Repository identity", MAX_IDENTITY_CHARACTERS);
    const args = [
      "agent-context", "--task", task,
      "--workspace-identity", workspaceIdentity,
      "--repository-identity", repositoryIdentity
    ];
    const appendPaths = (flag: string, values: readonly string[] | undefined, maximum: number): void => {
      if (!values) return;
      if (values.length > maximum) throw new Error(`Agent context ${flag} exceeds its item bound`);
      for (const value of values) args.push(flag, canonicalVaultRelativePath(requireBoundedString(value, `${flag} path`, 500)));
    };
    appendPaths("--active-file", options.activeFiles, 64);
    appendPaths("--open-tab", options.openTabs, 32);
    if (options.currentNote !== undefined) {
      args.push("--current-note", canonicalVaultRelativePath(requireBoundedString(options.currentNote, "Current note", 500)));
    }
    const selectedText = options.selectedText === undefined
      ? undefined
      : requireBoundedText(options.selectedText, "Selected text", MAX_SELECTED_TEXT_CHARACTERS);
    if (selectedText !== undefined) args.push("--selected-text", selectedText);
    const purpose = requireEnum(options.purpose ?? "answer", "Agent context purpose", ["answer", "verify", "quote", "historical", "legal", "debug", "freshness_check"] as const);
    const scope = requireEnum(options.scope ?? "project", "Agent context scope", ["personal", "project", "domain"] as const);
    const maxSensitivity = requireEnum(options.maxSensitivity ?? "private", "Agent context maximum sensitivity", ["public", "internal", "private", "restricted"] as const);
    const maxTokens = options.maxTokens ?? 4_000;
    if (!Number.isSafeInteger(maxTokens) || maxTokens < 128 || maxTokens > 32_000) {
      throw new Error("Agent context token budget is invalid");
    }
    args.push(
      "--purpose", purpose,
      "--scope", scope,
      "--max-sensitivity", maxSensitivity,
      "--max-tokens", String(maxTokens)
    );
    return this.knowledge(...args).then((result) => {
      parseAgentContextEnvelope(result.value);
      return result;
    });
  }

  wikiPage(wikiPath: string, cursor?: string): Promise<CommandResult> {
    const args = ["wiki", "page", "--vault", this.vaultRoot, "--wiki-path", canonicalWikiPath(wikiPath), "--limit", "20"];
    if (cursor !== undefined) args.push("--cursor", requireBoundedString(cursor, "Wiki cursor", MAX_CURSOR_CHARACTERS));
    return this.knowledge(...args);
  }

  wikiLinks(direction: "backlinks" | "outlinks", wikiPath: string, cursor?: string): Promise<CommandResult> {
    const selectedDirection = requireEnum(direction, "Wiki links direction", ["backlinks", "outlinks"] as const);
    const args = ["wiki", selectedDirection, "--vault", this.vaultRoot, "--wiki-path", canonicalWikiPath(wikiPath), "--limit", "20"];
    if (cursor !== undefined) args.push("--cursor", requireBoundedString(cursor, "Wiki cursor", MAX_CURSOR_CHARACTERS));
    return this.knowledge(...args);
  }

  sourceFragment(fragmentId: string): Promise<CommandResult> {
    return this.knowledge("source", "fragment", "--vault", this.vaultRoot,
      "--fragment-id", requireStableId(fragmentId, "Source Fragment ID"));
  }

  gaps(): Promise<CommandResult> {
    return this.knowledge("autonomy", "gaps", "--vault", this.vaultRoot);
  }
}
