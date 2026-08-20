# Living Wiki deterministic demo

Status: current working-tree example. It uses public synthetic text, the same
Compilation Coordinator as CLI/MCP/Python, and no model, network, or API key.

Run it only against a new or empty directory:

```bash
uv run python -m examples.living_wiki.run_demo \
  --workspace /tmp/deeplaw-living-wiki-demo
```

The command ingests and explicitly reviews one Markdown Source Revision, builds
the read-only Host handoff, creates a narrowly scoped semantic-compiler grant,
uses the deterministic fake MCP Agent to stage and atomically commit two
Knowledge Revisions through the existing Coordinator, rebuilds the Living Wiki,
verifies the Vault, runs one compiled-first Query and Context request, and drills
down from Wiki to the exact Source Revision. The returned JSON is validated by
`deeplaw.deterministic-fake-agent-compile/v1`.

The example is functional evidence for the local no-model loop. It is not a
real-host/model benchmark or competitive result.
