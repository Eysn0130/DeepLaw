import { createHash } from "node:crypto"

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

type RecordValue = Record<string, unknown>
type NativeEventName = (typeof NATIVE_EVENTS)[number]
type SpawnOptions = {
  cwd: string
  env: Record<string, string>
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
export type RouteResolver = (
  session: string | null,
  workspace: string | null,
) => Promise<RouteResolution>

export type NativeEventObservation = {
  event_type: string
  session_sha256: string | null
  parent_session_sha256: string | null
  parent_gap: "parent_absent" | null
  status: "observed" | "gap"
  gap: string | null
}

export type RouteResolution = {
  status: "exact" | "gap"
  session_sha256: string
  gap: string | null
  task_handle_sha256: string | null
  binding_sha256: string | null
  repository_sha256: string | null
  worktree_sha256: string | null
}

const FALLBACK_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
const MAX_ID_BYTES = 4096
const MAX_CLI_BYTES = 64 * 1024
const CLI_TIMEOUT_MS = 1500
const SHA256 = /^[0-9a-f]{64}$/
const GAP_CODES = new Set([
  "route_unbound",
  "route_ambiguous",
  "route_wrong_worktree",
  "route_stale",
  "route_forgotten",
  "workspace_snapshot_bound",
  "workspace_secret_unverifiable",
  "workspace_unavailable",
  "route_invalid",
  "route_identity_mismatch",
  "cli_unavailable",
  "cli_timeout",
  "cli_output_bound",
  "cli_output_invalid",
  "session_missing",
  "event_unknown",
  "checkpoint_grant_missing",
])

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
  return isRecord(value.event) ? value.event : value
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

function sessionDigest(input: unknown): string | null {
  if (!isRecord(input)) return null
  return sha256Text(firstText(input.sessionID, input.sessionId, input.session_id))
}

function closedChildEnv(): Record<string, string> {
  const processValue = (globalThis as Record<string, unknown>).process
  const processRecord = isRecord(processValue) ? processValue : {}
  const env = isRecord(processRecord.env) ? processRecord.env : {}
  const path = typeof env.PATH === "string" && env.PATH.length > 0 ? env.PATH : FALLBACK_PATH
  return {
    PATH: path,
    LANG: "C",
    LC_ALL: "C",
  }
}

export function childEnvironmentPolicy(): Record<string, string> {
  return { ...closedChildEnv() }
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

function validSha(value: unknown): string | null {
  return typeof value === "string" && SHA256.test(value) && value !== "0".repeat(64)
    ? value
    : null
}

function gapCode(value: unknown, fallback: string): string {
  if (isRecord(value) && typeof value.code === "string" && GAP_CODES.has(value.code)) {
    return value.code
  }
  return fallback
}

function parseRouteOutput(raw: Uint8Array, expectedSession: string): RouteResolution {
  let value: unknown
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw))
  } catch {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: "cli_output_invalid",
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  if (!isRecord(value) || value.host !== "opencode" || value.operation !== "resolve") {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: "route_invalid",
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  const returnedSession = value.session_sha256
  if (returnedSession !== expectedSession || value.write_performed !== false) {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: "route_identity_mismatch",
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  if (value.status === "gap") {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: gapCode(value.gap, "route_invalid"),
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  if (value.status !== "exact") {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: "route_invalid",
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  const taskHandle = boundedText(value.task_handle)
  const taskHandleDigest = sha256Text(taskHandle)
  const declaredTaskHandleDigest = validSha(value.task_handle_sha256)
  const binding = validSha(value.binding_sha256)
  const repository = validSha(value.repository_sha256)
  const worktree = validSha(value.worktree_sha256)
  if (
    taskHandleDigest === null ||
    declaredTaskHandleDigest === null ||
    taskHandleDigest !== declaredTaskHandleDigest ||
    binding === null ||
    repository === null ||
    worktree === null
  ) {
    return {
      status: "gap",
      session_sha256: expectedSession,
      gap: "route_invalid",
      task_handle_sha256: null,
      binding_sha256: null,
      repository_sha256: null,
      worktree_sha256: null,
    }
  }
  return {
    status: "exact",
    session_sha256: expectedSession,
    gap: null,
    task_handle_sha256: taskHandleDigest,
    binding_sha256: binding,
    repository_sha256: repository,
    worktree_sha256: worktree,
  }
}

function unavailableRoute(session: string | null, gap: string): RouteResolution {
  return {
    status: "gap",
    session_sha256: session ?? "gap:session_missing",
    gap,
    task_handle_sha256: null,
    binding_sha256: null,
    repository_sha256: null,
    worktree_sha256: null,
  }
}

export async function resolveHostSession(
  session: string | null,
  workspace: string | null,
  spawn: SpawnLike = defaultSpawn,
): Promise<RouteResolution> {
  if (session === null) return unavailableRoute(null, "session_missing")
  if (!SHA256.test(session) || session === "0".repeat(64)) {
    return unavailableRoute(session, "session_missing")
  }
  if (workspace === null || workspace.length === 0 || !absoluteWorkspace(workspace)) {
    return unavailableRoute(session, "workspace_unavailable")
  }
  const argv = [
    "deeplaw",
    "knowledge",
    "task",
    "resolve-host-session",
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
      stdout: "pipe",
      stderr: "pipe",
    })
  } catch {
    return unavailableRoute(session, "cli_unavailable")
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
      return unavailableRoute(session, "cli_timeout")
    }
    const [exitCode, stdout] = result
    if (exitCode !== 0) return unavailableRoute(session, "route_unbound")
    return parseRouteOutput(stdout, session)
  } catch (error) {
    child.kill?.()
    return unavailableRoute(
      session,
      error instanceof Error && error.message === "cli_output_bound"
        ? "cli_output_bound"
        : "cli_output_invalid",
    )
  } finally {
    if (timeout !== undefined) clearTimeout(timeout)
  }
}

function contextText(
  hook: string,
  session: string | null,
  route: RouteResolution,
  extraGap: string | null = null,
  parent: string | null = null,
): string {
  const gaps = [route.gap, extraGap].filter((value): value is string => value !== null)
  const mcpPrompt =
    session === null
      ? "mcp=gap:session_missing"
      : `If task context is needed, call the existing read-only knowledge_support tool using query or context with host_route={host:opencode,session_sha256:${session}}; keep returned text in the tool result.`
  const raw = [
    "DeepLaw OpenCode native seam (read-only)",
    `hook=${hook}; version=${OPENCODE_VERSION}; source_commit=${OPENCODE_SOURCE_COMMIT}`,
    `config_selector=${CONFIG_SELECTOR}; expected_response_model_id=${EXPECTED_RESPONSE_MODEL_ID}`,
    `session_sha256=${session ?? "gap:session_missing"}`,
    `parent_session_sha256=${parent ?? "gap:parent_absent"}`,
    `route_status=${route.status}; gap=${gaps.length > 0 ? gaps.join(",") : "none"}`,
    `task_handle_sha256=${route.task_handle_sha256 ?? "gap:route_unresolved"}`,
    `binding_sha256=${route.binding_sha256 ?? "gap:route_unresolved"}`,
    `repository_sha256=${route.repository_sha256 ?? "gap:route_unresolved"}`,
    `worktree_sha256=${route.worktree_sha256 ?? "gap:route_unresolved"}`,
    "write_performed=false",
    mcpPrompt,
  ].join("; ")
  if (new TextEncoder().encode(raw).byteLength <= MAX_CONTEXT_BYTES) return raw
  if (session === null) {
    return "DeepLaw OpenCode native seam Gap: context_bound; session_sha256=gap:session_missing; route_status=gap; write_performed=false; mcp=gap:session_missing"
  }
  return `DeepLaw OpenCode native seam Gap: context_bound; session_sha256=${session}; route_status=gap; host_route={host:opencode,session_sha256:${session}}; write_performed=false; call read-only knowledge_support query or context with that host_route.`
}

function appendContext(output: unknown, key: "system" | "context", context: string): void {
  if (!isRecord(output) || !Array.isArray(output[key])) return
  output[key].push(context)
}

export function createOpenCodeHooks(
  workspace: string | null,
  resolve: RouteResolver = resolveHostSession,
): Record<string, unknown> {
  const routeFor = async (session: string | null): Promise<RouteResolution> => {
    return resolve(session, workspace)
  }

  return {
    "chat.message": async (input: unknown) => {
      await routeFor(sessionDigest(input))
    },
    event: async (input: unknown) => {
      const observed = observeEvent(input)
      await routeFor(observed.session_sha256)
    },
    "experimental.chat.system.transform": async (input: unknown, output: unknown) => {
      const session = sessionDigest(input)
      const route = await routeFor(session)
      appendContext(output, "system", contextText("experimental.chat.system.transform", session, route))
    },
    "experimental.session.compacting": async (input: unknown, output: unknown) => {
      const session = sessionDigest(input)
      const route = await routeFor(session)
      appendContext(
        output,
        "context",
        contextText("experimental.session.compacting", session, route, "checkpoint_grant_missing"),
      )
    },
  }
}

export default async function DeepLawNativePlugin(input: unknown): Promise<Record<string, unknown>> {
  const pluginInput = isRecord(input) ? input : {}
  const workspace = firstText(pluginInput.worktree, pluginInput.directory)
  return createOpenCodeHooks(workspace)
}
