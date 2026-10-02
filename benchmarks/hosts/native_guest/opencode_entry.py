import json
import os
import pathlib
import sys

sys.path.insert(0, "/runtime")
import boundary_gate

boundary_gate.run()
owner = json.loads(pathlib.Path("/runtime/owner-input.json").read_bytes())
if owner.get("purpose") not in {
    "zero_model_preflight", "zero_model_fork_preflight", "native_model_probe",
}:
    raise ValueError("owner_purpose_invalid")
model_probe = owner["purpose"] == "native_model_probe"
fork_only = owner["purpose"] in {"zero_model_fork_preflight", "native_model_probe"}

root = pathlib.Path("/work")
for name in ("home", "config", "data", "cache", "state", "tmp", "config-dir"):
    (root / name).mkdir()
for directory in (root / "config/opencode", root / "config-dir", root / ".opencode"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node_modules").mkdir(exist_ok=True)
    dependency = {"@opencode-ai/plugin": "1.18.16-deeplaw.3"}
    (directory / "package.json").write_text(json.dumps({"dependencies": dependency}))
    (directory / "package-lock.json").write_text(
        json.dumps({"packages": {"": {"dependencies": dependency}}})
    )
os.symlink("/runtime/plugins", root / ".opencode/plugins", target_is_directory=True)
config = root / "opencode.json"
config.write_text(
    json.dumps(
        {
            "plugin": [],
            "mcp": {} if fork_only else {
                "maintenance_environment": {
                    "type": "local",
                    "command": ["/usr/bin/python3.12", "/runtime/mcp_client.py"],
                    "enabled": True,
                    "timeout": 10000,
                }
            },
            "share": "disabled",
            "autoupdate": False,
            **({
                "model": "deepseek/deepseek-v4-flash",
                "small_model": "deepseek/deepseek-v4-flash",
                "default_agent": "native_probe",
                "subagent_depth": 0,
                "snapshot": False,
                "instructions": [],
                "enabled_providers": ["deepseek"],
                "provider": {"deepseek": {
                    "options": {
                        "baseURL": "http://127.0.0.1:4100",
                        "apiKey": "{env:DEEPSEEK_API_KEY}",
                    },
                    "models": {
                        "deepseek-v4-flash": {"limit": {"context": 1_000_000, "output": 256}}
                    },
                }},
                "permission": {"*": "deny"},
                "agent": {"native_probe": {
                    "mode": "primary", "model": "deepseek/deepseek-v4-flash",
                    "variant": "max", "steps": 1, "permission": {"*": "deny"},
                    "prompt": "Answer the public probe directly without any tool.",
                }},
            } if model_probe else {}),
        }
    )
)
env = {
    "HOME": "/work/home",
    "PATH": "/usr/bin:/bin",
    "XDG_CONFIG_HOME": "/work/config",
    "XDG_DATA_HOME": "/work/data",
    "XDG_CACHE_HOME": "/work/cache",
    "XDG_STATE_HOME": "/work/state",
    "TMPDIR": "/work/tmp",
    "OPENCODE_CONFIG": str(config),
    "OPENCODE_CONFIG_DIR": "/work/config-dir",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "DEEPLAW_OPENCODE_MODEL_RECEIPT": "/work/tmp/native-events.jsonl",
    "NO_COLOR": "1",
    "CI": "1",
    **({"DEEPSEEK_API_KEY": owner["provider_nonce"]} if model_probe else {}),
}
os.chdir(root)
ready_output = os.open(
    root / "tmp/host-startup.log",
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
os.dup2(ready_output, 1)
os.close(ready_output)
os.execve(
    "/runtime/opencode",
    ["/runtime/opencode", "serve", "--hostname", "127.0.0.1", "--port", "4096"],
    env,
)
