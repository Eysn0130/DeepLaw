from __future__ import annotations

from typing import Literal

Shell = Literal["bash", "zsh", "fish"]

_ROOT_COMMANDS = (
    "init",
    "add",
    "sync",
    "review",
    "recall",
    "explain",
    "feedback",
    "status",
    "doctor",
    "open",
    "verify",
    "knowledge",
    "search",
    "get",
    "eval",
    "mcp",
    "completion",
)
_KNOWLEDGE_COMMANDS = (
    "init",
    "autonomy",
    "sink",
    "source",
    "structure",
    "review",
    "relation",
    "lineage",
    "recall",
    "diagnose-retrieval",
    "explain",
    "compare-retrieval",
    "retrieval-profile",
    "inbox",
    "job",
    "snapshot",
    "gc",
    "projection",
    "skill",
    "workbench",
    "doctor",
    "migrate",
    "mcp",
)


def shell_completion(shell: Shell) -> str:
    root = " ".join(_ROOT_COMMANDS)
    knowledge = " ".join(_KNOWLEDGE_COMMANDS)
    if shell == "bash":
        return f"""# DeepLaw deterministic completion
_deeplaw_complete() {{
  local current previous
  current="${{COMP_WORDS[COMP_CWORD]}}"
  previous="${{COMP_WORDS[COMP_CWORD-1]}}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "{root}" -- "$current") )
  elif [[ ${{COMP_WORDS[1]}} == knowledge && $COMP_CWORD -eq 2 ]]; then
    COMPREPLY=( $(compgen -W "{knowledge}" -- "$current") )
  elif [[ $previous == --vault || $previous == --source ||
          $previous == --git-repository || $previous == --output ||
          $previous == --projection || $previous == --bundle ||
          $previous == --snapshot || $previous == --install-root ]]; then
    COMPREPLY=( $(compgen -f -- "$current") )
  fi
}}
complete -F _deeplaw_complete deeplaw
"""
    if shell == "zsh":
        root_values = " ".join(f"'{item}:{item}'" for item in _ROOT_COMMANDS)
        knowledge_values = " ".join(f"'{item}:{item}'" for item in _KNOWLEDGE_COMMANDS)
        return f"""#compdef deeplaw
_deeplaw() {{
  local -a root_commands knowledge_commands
  root_commands=({root_values})
  knowledge_commands=({knowledge_values})
  if (( CURRENT == 2 )); then
    _describe 'command' root_commands
  elif [[ $words[2] == knowledge && CURRENT == 3 ]]; then
    _describe 'knowledge command' knowledge_commands
  else
    _arguments '*:path:_files'
  fi
}}
compdef _deeplaw deeplaw
"""
    if shell == "fish":
        lines = [
            "# DeepLaw deterministic completion",
            "complete -c deeplaw -f",
        ]
        lines.extend(
            f"complete -c deeplaw -n '__fish_use_subcommand' -a '{item}'" for item in _ROOT_COMMANDS
        )
        lines.extend(
            "complete -c deeplaw -n '__fish_seen_subcommand_from knowledge; "
            f"and not __fish_seen_subcommand_from {knowledge}' -a '{item}'"
            for item in _KNOWLEDGE_COMMANDS
        )
        return "\n".join(lines) + "\n"
    raise ValueError("unsupported completion shell")
