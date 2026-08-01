import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { isAbsolute, normalize, relative, resolve, sep } from "node:path";

export const WRITABLE_ROOTS = ["drafts", "notes", "sources/inbox"] as const;
export const READONLY_ROOTS = [".deeplaw", "canvas", "knowledge", "memory", "sources", "wiki"] as const;
const MAX_STDOUT_BYTES = 65_536;
const MAX_STDERR_BYTES = 16_384;

export function canonicalVaultRelativePath(value: string): string {
  if (!value || value.includes("\0") || isAbsolute(value)) {
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
    return isAbsolute(value) ? "[local path redacted]" : value;
  }
  if (Array.isArray(value)) return value.map(sanitizeProviderValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      /token|secret|private.?key/i.test(key) ? "redacted" : key,
      /token|secret|private.?key/i.test(key) ? "[redacted]" : sanitizeProviderValue(item)
    ]));
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

  query(query: string, purpose = "answer"): Promise<CommandResult> {
    return this.knowledge("query", "--vault", this.vaultRoot, "--query", query,
      "--purpose", purpose, "--query-plan-version", "5");
  }

  context(task: string): Promise<CommandResult> {
    return this.knowledge("autonomy", "context", "--vault", this.vaultRoot,
      "--task", task, "--confirm-no-case-data");
  }

  compilationStatus(runId?: string): Promise<CommandResult> {
    return runId
      ? this.knowledge("compile", "status", "--vault", this.vaultRoot, "--run-id", runId)
      : this.knowledge("source", "list", "--vault", this.vaultRoot);
  }

  beginCompilation(sourceRevisionId: string, grantId: string): Promise<CommandResult> {
    if (!grantId) throw new Error("An owner-created compiler grant is required");
    return this.knowledge("compile", "begin", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--source-revision-id", sourceRevisionId,
      "--compiler-profile", "living-wiki-agent", "--compiler-profile-version", "2",
      "--host-identity", "obsidian", "--confirm-no-case-data");
  }

  resumeProjection(runId: string, grantId: string): Promise<CommandResult> {
    return this.knowledge("compile", "resume", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--run-id", runId, "--project", "--confirm-no-case-data");
  }

  refresh(
    sourceRevisionId: string,
    grantId: string,
    replacementSourceRevisionId?: string
  ): Promise<CommandResult> {
    if (!grantId) throw new Error("An owner-created compiler grant is required");
    const args = ["compile", "refresh", "--vault", this.vaultRoot,
      "--grant-id", grantId, "--source-revision-id", sourceRevisionId,
      "--confirm-no-case-data"];
    if (replacementSourceRevisionId) args.push("--replacement-source-revision-id", replacementSourceRevisionId);
    return this.knowledge(...args);
  }

  gaps(): Promise<CommandResult> {
    return this.knowledge("autonomy", "gaps", "--vault", this.vaultRoot);
  }
}
