# Host configuration

Keep `knowledge_support` and `knowledge_sink` as separate local stdio processes. Replace
`<owner-created-grant-id>` locally; never commit the real grant ID or capability token.

## Codex and Claude Code

The installed `deeplaw-knowledge-os` plugin supplies the read-only server:

```json
{
  "mcpServers": {
    "deeplaw-knowledge": {
      "command": "deeplaw",
      "args": ["knowledge", "mcp", "--stdio"]
    }
  }
}
```

Add the Sink only to an explicit compiler profile or project-local owner configuration:

```json
{
  "mcpServers": {
    "deeplaw-knowledge-sink": {
      "command": "deeplaw",
      "args": [
        "knowledge", "sink", "mcp",
        "--grant-id", "<owner-created-grant-id>",
        "--stdio"
      ]
    }
  }
}
```

Invoke `$compile-living-wiki` in Codex or
`/deeplaw-knowledge-os:compile-living-wiki` in Claude Code. Host tool prefixes may differ; the
server leaf names remain exactly `knowledge_support` and `knowledge_sink`.

## OpenCode

Merge, do not replace, the existing OpenCode configuration. Keep the read-only server from
`adapters/opencode/knowledge-os.jsonc` and add an explicitly enabled Sink:

```jsonc
{
  "mcp": {
    "deeplaw_knowledge": {
      "type": "local",
      "command": ["deeplaw", "knowledge", "mcp", "--stdio"],
      "enabled": true
    },
    "deeplaw_knowledge_sink": {
      "type": "local",
      "command": [
        "deeplaw", "knowledge", "sink", "mcp",
        "--grant-id", "<owner-created-grant-id>", "--stdio"
      ],
      "enabled": true
    }
  }
}
```

Copy the Skill into `.opencode/skills/compile-living-wiki/SKILL.md`. Start from
`adapters/opencode/knowledge-compiler.example.jsonc` and
`adapters/opencode/agents/deeplaw-compiler.md`; replace the grant placeholder only in the local
owner configuration. The overlay grants the two exact MCP leaf calls and explicitly overrides the
read-only profile's wildcard denial. Do not grant shell, arbitrary file writes, grant
administration, or Legal Pack mutation.

## Preflight

Run these owner-side checks before a compiler task:

```bash
deeplaw knowledge sink enable \
  --writer-id compiler-agent \
  --scope project \
  --profile compiler
deeplaw knowledge sink status --grant-id <owner-created-grant-id>
deeplaw knowledge autonomy verify
deeplaw knowledge --format json compile status --run-id <existing-run-id>
```

The grant operation list must contain only the required compilation operations. A separate
backfill workflow should use a separate backfill grant.
