import { createHash } from "node:crypto"
import { appendFile } from "node:fs/promises"
import { dirname, resolve, sep } from "node:path"

export const OPENCODE_VERSION = "1.18.16"
export const OPENCODE_SOURCE_COMMIT = "a3647eb025c7615159d417dcc49fc39fdaeba65b"
export const CONFIG_SELECTOR = "deepseek/deepseek-v4-flash"
export const EXPECTED_RESPONSE_MODEL_ID = "deepseek-v4-flash"
export const MAX_CONTEXT_BYTES = 2048

export const NATIVE_EVENTS = [
  "session.created",
  "session.updated",
  "session.compacted",
] as const

const HOST_CONTINUITY_SCHEMA = "deeplaw.host-continuity-capsule/v1"
const MAX_CAPSULE_BYTES = 1400
const MAX_CLI_BYTES = 64 * 1024
const MAX_ID_BYTES = 4096
const CLI_TIMEOUT_MS = 1500
const SHA256 = /^[0-9a-f]{64}$/
const SHA256_TEXT = /[0-9a-f]{64}/i
const GAP_CODE = /^[a-z0-9][a-z0-9_.:-]{0,99}$/
const ABSOLUTE_PATH_TEXT = /(?:^|[\s"'=:(])\/(?!\/)[^\s"'=;:)]+/i
const WINDOWS_PATH_TEXT = /(?:^|[\s"'=:(])[A-Za-z]:[\\/]/
const SECRET_TEXT =
  /(?:-----BEGIN|\b(?:api[_-]?key|password|client[_-]?secret|access[_-]?token)\s*[:=]|\b(?:authorization|bearer)(?:\s*[:=]\s*|\s+)[^\s"']{8,}|\b(?:sk|ghp|xoxb)-[A-Za-z0-9_-]{12,})/i

type RecordValue = Record<string, unknown>
type NativeEventName = (typeof NATIVE_EVENTS)[number]
type SpawnOptions = {
  cwd: string
  env: Record<string, string>
  stdin: "ignore"
  stdout: "pipe"
  stderr: "pipe"
}
type SpawnedProcess = {
  stdout?: ReadableStream<Uint8Array> | null
  stderr?: ReadableStream<Uint8Array> | null
  exited: Promise<number>
  kill?: () => void
}
export type SpawnLike = (argv: string[], options: SpawnOptions) => SpawnedProcess

export type ContinuityCitation = { locator: string }
export type ContinuityStatement = {
  content: string
  authority: string
  legal_authority: false
  valid_from: string | null
  valid_to: string | null
  citations: ContinuityCitation[]
}
export type ContinuityGap = { code: string; message?: string }
export type ContinuityConflict = { summary: string }
export type ContinuityCapsule = {
  schema_version: typeof HOST_CONTINUITY_SCHEMA
  status: "admitted" | "gap"
  statements: ContinuityStatement[]
  gaps: ContinuityGap[]
  conflicts: ContinuityConflict[]
  write_performed: false
}
export type ContinuityResolver = (
  session: string | null,
  workspace: string | null,
) => Promise<ContinuityCapsule>

export type NativeEventObservation = {
  event_type: string
  session_sha256: string | null
  parent_session_sha256: string | null
  parent_gap: "parent_absent" | null
  status: "observed" | "gap"
  gap: string | null
}

export type ResponseModelObservation = {
  schema_version: "deeplaw.opencode-model-observation/v1"
  event_type: "message.updated"
  session_sha256: string
  message_sha256: string
  role: "assistant"
  provider_id: string
  model_id: string
  summary: boolean
  mode: string | null
  finish: string | null
  tokens: {
    input: number
    output: number
    reasoning: number
    total: number
    cache: { read: number; write: number }
  }
}

export type ContinuityDeliveryObservation = {
  schema_version: "deeplaw.opencode-continuity-delivery-observation/v1"
  event_type:
    | "experimental.chat.system.transform"
    | "experimental.session.compacting"
  session_sha256: string
  context_sha256: string
  context_bytes: number
  status: "admitted" | "gap"
  statement_count: number
  gap_codes: string[]
  conflict_count: number
}

export type OpenCodeHostObservation =
  | ResponseModelObservation
  | ContinuityDeliveryObservation

const FALLBACK_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function boundedText(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0 || value.includes("\u0000")) {
    return null
  }
  if (new TextEncoder().encode(value).byteLength > MAX_ID_BYTES) {
    return null
  }
  return value
}

function sha256Text(value: unknown): string | null {
  const text = boundedText(value)
  if (text === null) return null
  return createHash("sha256").update(text, "utf8").digest("hex")
}

function firstText(...values: unknown[]): string | null {
  for (const value of values) {
    const text = boundedText(value)
    if (text !== null) return text
  }
  return null
}

function absoluteWorkspace(value: string): boolean {
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value)
}

function eventValue(value: unknown): RecordValue {
  if (!isRecord(value)) return {}
  const event = isRecord(value.event) ? value.event : value
  const properties = isRecord(event.properties) ? event.properties : event
  return { ...properties, type: event.type }
}

export function observeEvent(input: unknown): NativeEventObservation {
  const event = eventValue(input)
  const type = boundedText(event.type)
  const info = isRecord(event.info) ? event.info : {}
  const session = firstText(info.id, event.sessionID, event.sessionId)
  const parent = boundedText(info.parentID)
  const supported = type !== null && NATIVE_EVENTS.includes(type as NativeEventName)
  let gap: string | null = supported ? null : "event_unknown"
  if (session === null) gap = gap ?? "session_missing"
  return {
    event_type: type ?? "gap:event_unknown",
    session_sha256: sha256Text(session),
    parent_session_sha256: sha256Text(parent),
    parent_gap: parent === null ? "parent_absent" : null,
    status: gap === null ? "observed" : "gap",
    gap,
  }
}

function nonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
}

export function observeResponseModel(input: unknown): ResponseModelObservation | null {
  const event = eventValue(input)
  if (event.type !== "message.updated") return null
  const info = isRecord(event.info) ? event.info : {}
  if (info.role !== "assistant") return null
  const session = firstText(info.sessionID, event.sessionID, event.sessionId)
  const message = boundedText(info.id)
  const provider = boundedText(info.providerID)
  const model = boundedText(info.modelID)
  const tokens = isRecord(info.tokens) ? info.tokens : {}
  const cache = isRecord(tokens.cache) ? tokens.cache : {}
  if (
    session === null ||
    message === null ||
    provider === null ||
    model === null ||
    !nonnegativeInteger(tokens.input) ||
    !nonnegativeInteger(tokens.output) ||
    !nonnegativeInteger(tokens.reasoning) ||
    !nonnegativeInteger(cache.read) ||
    !nonnegativeInteger(cache.write)
  ) {
    return null
  }
  const mode = info.mode === undefined ? null : boundedText(info.mode)
  const finish = info.finish === undefined ? null : boundedText(info.finish)
  if ((info.mode !== undefined && mode === null) || (info.finish !== undefined && finish === null)) {
    return null
  }
  return {
    schema_version: "deeplaw.opencode-model-observation/v1",
    event_type: "message.updated",
    session_sha256: createHash("sha256").update(session, "utf8").digest("hex"),
    message_sha256: createHash("sha256").update(message, "utf8").digest("hex"),
    role: "assistant",
    provider_id: provider,
    model_id: model,
    summary: info.summary === true,
    mode,
    finish,
    tokens: {
      input: tokens.input,
      output: tokens.output,
      reasoning: tokens.reasoning,
      total: tokens.input + tokens.output + tokens.reasoning + cache.read + cache.write,
      cache: { read: cache.read, write: cache.write },
    },
  }
}

function modelReceiptPath(): string | null {
  const processValue = (globalThis as Record<string, unknown>).process
  const processRecord = isRecord(processValue) ? processValue : {}
  const env = isRecord(processRecord.env) ? processRecord.env : {}
  const raw = boundedText(env.DEEPLAW_OPENCODE_MODEL_RECEIPT)
  const temp = boundedText(env.TMPDIR ?? env.TMP ?? env.TEMP)
  if (raw === null || temp === null || !absoluteWorkspace(raw) || !absoluteWorkspace(temp)) {
    return null
  }
  const target = resolve(raw)
  const root = resolve(temp)
  if (dirname(target) !== root || !target.startsWith(root + sep)) return null
  return target
}

export async function recordHostObservation(observation: OpenCodeHostObservation): Promise<void> {
  const target = modelReceiptPath()
  if (target === null) return
  await appendFile(target, JSON.stringify(observation) + "\n", {
    encoding: "utf8",
    flag: "a",
    mode: 0o600,
  })
}

export const recordResponseModel = recordHostObservation

function sessionDigest(input: unknown): string | null {
  if (!isRecord(input)) return null
  return sha256Text(firstText(input.sessionID, input.sessionId, input.session_id))
}

function closedChildEnv(): Record<string, string> {
  const processValue = (globalThis as Record<string, unknown>).process
  const processRecord = isRecord(processValue) ? processValue : {}
  const env = isRecord(processRecord.env) ? processRecord.env : {}
  const path = typeof env.PATH === "string" && env.PATH.length > 0 ? env.PATH : FALLBACK_PATH
  const result: Record<string, string> = {
    PATH: path,
    LANG: "C",
    LC_ALL: "C",
  }
  const vault = boundedText(env.DEEPLAW_KNOWLEDGE_VAULT)
  if (vault !== null && absoluteWorkspace(vault)) {
    result.DEEPLAW_KNOWLEDGE_VAULT = vault
  }
  return result
}

export function childEnvironmentPolicy(): Record<string, string> {
  return { ...closedChildEnv() }
}

function vaultFromEnvironment(): string | null {
  const environment = childEnvironmentPolicy()
  const vault = environment.DEEPLAW_KNOWLEDGE_VAULT
  return vault !== undefined && absoluteWorkspace(vault) ? vault : null
}

function defaultSpawn(argv: string[], options: SpawnOptions): SpawnedProcess {
  const bun = (globalThis as Record<string, unknown>).Bun
  if (!isRecord(bun) || typeof bun.spawn !== "function") {
    throw new Error("Bun runtime is unavailable")
  }
  return bun.spawn(argv, options) as SpawnedProcess
}

async function readBounded(stream: ReadableStream<Uint8Array> | null | undefined): Promise<Uint8Array> {
  if (stream === null || stream === undefined) return new Uint8Array()
  const reader = stream.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  try {
    while (true) {
      const next = await reader.read()
      if (next.done) break
      const chunk = next.value
      total += chunk.byteLength
      if (total > MAX_CLI_BYTES) {
        await reader.cancel()
        throw new Error("cli_output_bound")
      }
      chunks.push(chunk)
    }
  } finally {
    reader.releaseLock()
  }
  const result = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    result.set(chunk, offset)
    offset += chunk.byteLength
  }
  return result
}

function exactKeys(value: RecordValue, expected: readonly string[]): boolean {
  const keys = Object.keys(value)
  return keys.length === expected.length && keys.every((key) => expected.includes(key))
}

function unsafeProviderText(value: string): boolean {
  return SHA256_TEXT.test(value) || ABSOLUTE_PATH_TEXT.test(value) || WINDOWS_PATH_TEXT.test(value) || SECRET_TEXT.test(value)
}

function providerText(value: unknown, maximum: number): string | null {
  if (typeof value !== "string" || value.length === 0 || value.includes("\u0000")) return null
  if (new TextEncoder().encode(value).byteLength > maximum) return null
  if (
    [...value].some(
      (character) =>
        character.charCodeAt(0) < 0x20 && character !== "\n" && character !== "\t",
    )
  ) {
    return null
  }
  if (unsafeProviderText(value)) return null
  return value
}

function nullableProviderText(value: unknown, maximum: number): boolean {
  return value === null || providerText(value, maximum) !== null
}

function continuityGap(...codes: string[]): ContinuityCapsule {
  const selected = [...new Set(codes.filter((code) => code.length > 0))].slice(0, 8)
  return {
    schema_version: HOST_CONTINUITY_SCHEMA,
    status: "gap",
    statements: [],
    gaps: (selected.length > 0 ? selected : ["continuity_unavailable"]).map((code) => ({ code })),
    conflicts: [],
    write_performed: false,
  }
}

function validateCapsule(value: unknown): ContinuityCapsule | null {
  if (
    !isRecord(value) ||
    !exactKeys(value, ["schema_version", "status", "statements", "gaps", "conflicts", "write_performed"])
  ) {
    return null
  }
  if (
    value.schema_version !== HOST_CONTINUITY_SCHEMA ||
    (value.status !== "admitted" && value.status !== "gap") ||
    value.write_performed !== false
  ) {
    return null
  }
  const statements = value.statements
  const gaps = value.gaps
  const conflicts = value.conflicts
  if (
    !Array.isArray(statements) ||
    statements.length > 2 ||
    !Array.isArray(gaps) ||
    gaps.length > 8 ||
    !Array.isArray(conflicts) ||
    conflicts.length > 4 ||
    (value.status === "admitted" && statements.length < 1) ||
    (value.status === "gap" && (statements.length > 0 || gaps.length < 1))
  ) {
    return null
  }

  const selectedStatements: ContinuityStatement[] = []
  for (const statement of statements) {
    if (
      !isRecord(statement) ||
      !exactKeys(statement, ["content", "authority", "legal_authority", "valid_from", "valid_to", "citations"])
    ) {
      return null
    }
    const content = providerText(statement.content, 512)
    const authority = providerText(statement.authority, 100)
    const citations = statement.citations
    if (
      content === null ||
      authority === null ||
      statement.legal_authority !== false ||
      !nullableProviderText(statement.valid_from, 100) ||
      !nullableProviderText(statement.valid_to, 100) ||
      !Array.isArray(citations) ||
      citations.length > 2
    ) {
      return null
    }
    const selectedCitations: ContinuityCitation[] = []
    for (const citation of citations) {
      if (!isRecord(citation) || !exactKeys(citation, ["locator"])) return null
      const locator = providerText(citation.locator, 200)
      if (locator === null) return null
      selectedCitations.push({ locator })
    }
    selectedStatements.push({
      content,
      authority,
      legal_authority: false,
      valid_from: statement.valid_from as string | null,
      valid_to: statement.valid_to as string | null,
      citations: selectedCitations,
    })
  }

  const selectedGaps: ContinuityGap[] = []
  for (const gap of gaps) {
    if (
      !isRecord(gap) ||
      (Object.keys(gap).length !== 1 && Object.keys(gap).length !== 2) ||
      !Object.keys(gap).every((key) => key === "code" || key === "message")
    ) {
      return null
    }
    const code = providerText(gap.code, 100)
    if (code === null || !GAP_CODE.test(code)) return null
    if ("message" in gap && providerText(gap.message, 160) === null) return null
    const selected: ContinuityGap = { code }
    if ("message" in gap) selected.message = gap.message as string
    selectedGaps.push(selected)
  }

  const selectedConflicts: ContinuityConflict[] = []
  for (const conflict of conflicts) {
    if (!isRecord(conflict) || !exactKeys(conflict, ["summary"])) return null
    const summary = providerText(conflict.summary, 160)
    if (summary === null) return null
    selectedConflicts.push({ summary })
  }

  const selected: ContinuityCapsule = {
    schema_version: HOST_CONTINUITY_SCHEMA,
    status: value.status,
    statements: selectedStatements,
    gaps: selectedGaps,
    conflicts: selectedConflicts,
    write_performed: false,
  }
  const encoded = new TextEncoder().encode(JSON.stringify(selected))
  if (encoded.byteLength > MAX_CAPSULE_BYTES || unsafeProviderText(JSON.stringify(selected))) return null
  return selected
}

export function validateContinuityCapsule(value: unknown): ContinuityCapsule | null {
  return validateCapsule(value)
}

export function parseContinuityOutput(raw: Uint8Array): ContinuityCapsule | null {
  if (raw.byteLength === 0 || raw.byteLength > MAX_CLI_BYTES) return null
  let value: unknown
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw))
  } catch {
    return null
  }
  return validateCapsule(value)
}

export async function resolveHostContinuity(
  session: string | null,
  workspace: string | null,
  spawn: SpawnLike = defaultSpawn,
): Promise<ContinuityCapsule> {
  if (session === null || !SHA256.test(session) || session === "0".repeat(64)) {
    return continuityGap("session_missing")
  }
  if (workspace === null || workspace.length === 0 || !absoluteWorkspace(workspace)) {
    return continuityGap("workspace_unavailable")
  }
  const vault = vaultFromEnvironment()
  if (vault === null) return continuityGap("vault_unavailable")
  const argv = [
    "deeplaw",
    "knowledge",
    "--format",
    "jsonl",
    "task",
    "resolve-host-continuity",
    "--vault",
    vault,
    "--host",
    "opencode",
    "--session-sha256",
    session,
    "--workspace",
    workspace,
  ]
  let child: SpawnedProcess
  try {
    child = spawn(argv, {
      cwd: workspace,
      env: closedChildEnv(),
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
    })
  } catch {
    return continuityGap("continuity_resolve_failed")
  }
  const outputPromise = readBounded(child.stdout)
  const errorPromise = readBounded(child.stderr)
  const resultPromise = Promise.all([child.exited, outputPromise, errorPromise])
  let timeout: ReturnType<typeof setTimeout> | undefined
  try {
    const result = await Promise.race([
      resultPromise,
      new Promise<null>((resolve) => {
        timeout = setTimeout(() => resolve(null), CLI_TIMEOUT_MS)
      }),
    ])
    if (result === null) {
      child.kill?.()
      return continuityGap("continuity_resolve_timeout")
    }
    const [exitCode, stdout] = result
    if (exitCode !== 0) return continuityGap("continuity_resolve_failed")
    return parseContinuityOutput(stdout) ?? continuityGap("continuity_capsule_invalid")
  } catch (error) {
    child.kill?.()
    return continuityGap(
      error instanceof Error && error.message === "cli_output_bound"
        ? "continuity_capsule_bound"
        : "continuity_resolve_failed",
    )
  } finally {
    if (timeout !== undefined) clearTimeout(timeout)
  }
}

function withCheckpointGap(capsule: ContinuityCapsule): ContinuityCapsule {
  if (capsule.gaps.some((gap) => gap.code === "checkpoint_grant_missing")) return capsule
  if (capsule.gaps.length >= 8) return continuityGap("checkpoint_grant_missing")
  const selected = {
    ...capsule,
    gaps: [...capsule.gaps, { code: "checkpoint_grant_missing" }],
  }
  return validateCapsule(selected) ?? continuityGap("checkpoint_grant_missing")
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean" || typeof value === "number") {
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`
  }
  throw new TypeError("continuity context contains an unsupported JSON value")
}

function contextText(capsule: ContinuityCapsule): string {
  const selected = validateCapsule(capsule) ?? continuityGap("continuity_capsule_invalid")
  const rendered = canonicalJson(selected)
  if (new TextEncoder().encode(rendered).byteLength <= MAX_CONTEXT_BYTES) return rendered
  return canonicalJson(continuityGap("continuity_capsule_bound"))
}

function continuityDeliveryObservation(
  input: unknown,
  eventType: ContinuityDeliveryObservation["event_type"],
  capsule: ContinuityCapsule,
  context: string,
): ContinuityDeliveryObservation | null {
  const session = sessionDigest(input)
  if (session === null) return null
  const encoded = new TextEncoder().encode(context)
  return {
    schema_version: "deeplaw.opencode-continuity-delivery-observation/v1",
    event_type: eventType,
    session_sha256: session,
    context_sha256: createHash("sha256").update(context, "utf8").digest("hex"),
    context_bytes: encoded.byteLength,
    status: capsule.status,
    statement_count: capsule.statements.length,
    gap_codes: [...new Set(capsule.gaps.map((gap) => gap.code))].sort(),
    conflict_count: capsule.conflicts.length,
  }
}

function appendContext(output: unknown, key: "system" | "context", context: string): void {
  if (!isRecord(output) || !Array.isArray(output[key])) return
  output[key].push(context)
}

export function createOpenCodeHooks(
  workspace: string | null,
  resolve: ContinuityResolver = resolveHostContinuity,
  record: (observation: OpenCodeHostObservation) => Promise<void> = recordHostObservation,
): Record<string, unknown> {
  const capsuleFor = async (session: string | null): Promise<ContinuityCapsule> => {
    try {
      return validateCapsule(await resolve(session, workspace)) ?? continuityGap("continuity_capsule_invalid")
    } catch {
      return continuityGap("continuity_resolve_failed")
    }
  }

  return {
    "chat.message": async (input: unknown) => {
      await capsuleFor(sessionDigest(input))
    },
    event: async (input: unknown) => {
      const response = observeResponseModel(input)
      if (response !== null) await record(response)
      const observed = observeEvent(input)
      if (observed.status === "observed") await capsuleFor(observed.session_sha256)
    },
    "experimental.chat.system.transform": async (input: unknown, output: unknown) => {
      const capsule = await capsuleFor(sessionDigest(input))
      const context = contextText(capsule)
      appendContext(output, "system", context)
      const delivery = continuityDeliveryObservation(
        input,
        "experimental.chat.system.transform",
        capsule,
        context,
      )
      if (delivery !== null) await record(delivery)
    },
    "experimental.session.compacting": async (input: unknown, output: unknown) => {
      const capsule = withCheckpointGap(await capsuleFor(sessionDigest(input)))
      const context = contextText(capsule)
      appendContext(output, "context", context)
      const delivery = continuityDeliveryObservation(
        input,
        "experimental.session.compacting",
        capsule,
        context,
      )
      if (delivery !== null) await record(delivery)
    },
  }
}

export default {
  id: "deeplaw-native",
  server: async (input: unknown): Promise<Record<string, unknown>> => {
    const pluginInput = isRecord(input) ? input : {}
    const workspace = firstText(pluginInput.worktree, pluginInput.directory)
    return createOpenCodeHooks(workspace)
  },
}
