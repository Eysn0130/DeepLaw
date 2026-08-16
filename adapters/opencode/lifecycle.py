"""OpenCode native lifecycle entrypoint for one owner-registered DeepLaw task."""

from deeplaw.host_lifecycle import adapter_main

if __name__ == "__main__":
    raise SystemExit(adapter_main(host="opencode"))
