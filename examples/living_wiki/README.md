# Living Wiki deterministic demo

Status: current working-tree example. It uses public synthetic text, the same
Compilation Coordinator as CLI/MCP/Python, and no model, network, or API key.

Run it only against a new or empty directory:

```bash
uv run python -m examples.living_wiki.run_demo \
  --workspace /tmp/deeplaw-living-wiki-demo
```

The command ingests one Markdown Source Revision, creates a narrowly scoped
compiler grant, uses the deterministic fake Agent to stage and atomically commit
two Knowledge Revisions, rebuilds the Living Wiki, verifies the Vault, and runs
one compiled-first query. The returned JSON is validated by
`deeplaw.deterministic-fake-agent-compile/v1`.

The example is functional evidence for the local no-model loop. It is not a
real-host/model benchmark or competitive result.
