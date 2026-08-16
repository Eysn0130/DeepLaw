#!/usr/bin/env node

// This probe imports the exact frozen Tolaria MCP tool service.  Its stdout is
// a deliberately closed, relative-path-only summary; returned absolute paths
// from Tolaria's UI/file helpers are observed only inside the process.

import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

function parseArguments(argv) {
  const values = {}
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index]
    if (!name.startsWith('--')) throw new Error('invalid argument')
    const value = argv[index + 1]
    if (!value || value.startsWith('--')) throw new Error('missing argument')
    values[name.slice(2)] = value
    index += 1
  }
  return values
}

function isRelativeNotePath(value) {
  if (typeof value !== 'string' || !value || value.includes('\\') || path.isAbsolute(value)) {
    return false
  }
  const parts = value.split('/')
  return parts.every(part => part && part !== '.' && part !== '..')
}

function hashBytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function cjkCount(value) {
  return (value.match(/[\u3400-\u9fff]/g) ?? []).length
}

function tableCount(value) {
  return value
    .split(/\r?\n/)
    .filter(line => line.trim().startsWith('|') && line.trim().endsWith('|')).length
}

function fencedBlockCount(value) {
  return value.match(/```[\s\S]*?```/g)?.length ?? 0
}

function aliasCount(frontmatter) {
  if (!frontmatter || typeof frontmatter !== 'object') return 0
  const aliases = frontmatter.aliases
  if (Array.isArray(aliases)) return aliases.filter(value => typeof value === 'string').length
  return typeof aliases === 'string' && aliases ? 1 : 0
}

function safePath(value, fallback = 'unknown') {
  return isRelativeNotePath(value) ? value : fallback
}

function failure(pathValue = 'unknown') {
  return {
    status: 'failed',
    path: safePath(pathValue),
    before_sha256: null,
    after_sha256: null,
    read_count: 0,
    open_count: 0,
    update_count: 0,
    table_count: 0,
    alias_count: 0,
    fenced_block_count: 0,
    cjk_count: 0,
  }
}

async function run(values) {
  const checkout = typeof values['tolaria-checkout'] === 'string' ? values['tolaria-checkout'] : ''
  const vault = typeof values.vault === 'string' ? values.vault : ''
  const notePath = values.path
  if (!checkout || !vault || !isRelativeNotePath(notePath)) throw new Error('invalid probe input')
  const contentBase64 = values['content-base64']
  const expectedSha256 = values['expected-sha256']
  const markersBase64 = values['markers-base64']
  if (!contentBase64 || !/^[0-9a-f]{64}$/.test(expectedSha256 ?? '') || !markersBase64) {
    throw new Error('invalid probe payload')
  }
  const content = Buffer.from(contentBase64, 'base64').toString('utf8')
  const markers = JSON.parse(Buffer.from(markersBase64, 'base64').toString('utf8'))
  if (!Array.isArray(markers) || markers.some(marker => typeof marker !== 'string')) {
    throw new Error('invalid markers')
  }

  const toolServiceUrl = pathToFileURL(path.join(checkout, 'mcp-server', 'tool-service.js')).href
  const module = await import(toolServiceUrl)
  if (typeof module.createMcpToolService !== 'function') throw new Error('tool service export missing')
  const service = module.createMcpToolService({
    resolveVaultPaths: () => [vault],
    emitUiAction: () => {},
  })

  const noteFile = path.join(vault, ...notePath.split('/'))
  const beforeBytes = await readFile(noteFile)
  const beforeSha256 = hashBytes(beforeBytes)
  const first = await service.readNote({ path: notePath, vaultPath: vault })
  service.openNoteInEditor({ path: notePath, vaultPath: vault })
  await service.updateNote({
    path: notePath,
    vaultPath: vault,
    content,
    expectedMtime: first.mtimeMs,
  })
  const second = await service.readNote({ path: notePath, vaultPath: vault })
  const afterBytes = await readFile(noteFile)
  const afterSha256 = hashBytes(afterBytes)
  const observedBody = `${JSON.stringify(second.frontmatter ?? {})}\n${second.content ?? ''}`
  const allMarkersPresent = markers.every(marker => observedBody.includes(marker))
  const passed = (
    beforeSha256 !== ''
    && afterSha256 === expectedSha256
    && allMarkersPresent
    && tableCount(second.content ?? '') >= 2
    && aliasCount(second.frontmatter) >= 2
    && fencedBlockCount(second.content ?? '') >= 1
    && cjkCount(observedBody) >= 1
  )
  if (!passed) throw new Error('round-trip assertions failed')
  return {
    status: 'passed',
    path: notePath,
    before_sha256: beforeSha256,
    after_sha256: afterSha256,
    read_count: 2,
    open_count: 1,
    update_count: 1,
    table_count: tableCount(second.content ?? ''),
    alias_count: aliasCount(second.frontmatter),
    fenced_block_count: fencedBlockCount(second.content ?? ''),
    cjk_count: cjkCount(observedBody),
  }
}

async function runSilently(values) {
  const originalConsoleLog = console.log
  const originalConsoleError = console.error
  const originalStdoutWrite = process.stdout.write
  const originalStderrWrite = process.stderr.write
  console.log = () => {}
  console.error = () => {}
  process.stdout.write = () => true
  process.stderr.write = () => true
  try {
    return await run(values)
  } finally {
    console.log = originalConsoleLog
    console.error = originalConsoleError
    process.stdout.write = originalStdoutWrite
    process.stderr.write = originalStderrWrite
  }
}

let values = {}
try {
  values = parseArguments(process.argv.slice(2))
  const result = await runSilently(values)
  process.stdout.write(`${JSON.stringify(result)}\n`)
} catch {
  // Never expose stack traces, imported-service errors, vault roots, or
  // returned absolutePath/targetPath values through this probe.
  const result = failure(values.path)
  process.stdout.write(`${JSON.stringify(result)}\n`)
  process.exitCode = 1
}
