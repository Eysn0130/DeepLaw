from __future__ import annotations

import argparse
from pathlib import Path

from deeplaw.util import canonical_json, sha256_bytes

_MUTATIONS = (
    ("壹", "一", "financial_uppercase"),
    ("万", "方", "shape_confusable"),
    ("未", "末", "shape_confusable"),
    ("己", "已", "shape_confusable"),
    ("0", "O", "latin_digit_confusable"),
)


def generate(text: str) -> dict:
    cases = []
    for source, replacement, mutation_type in _MUTATIONS:
        if source not in text:
            continue
        mutated = text.replace(source, replacement, 1)
        cases.append(
            {
                "mutation_type": mutation_type,
                "source_character": source,
                "replacement_character": replacement,
                "original_sha256": sha256_bytes(text.encode("utf-8")),
                "mutated_sha256": sha256_bytes(mutated.encode("utf-8")),
                "mutated_text": mutated,
                "expected_capability": "extraction:ocr_unreviewed",
                "expected_answerability": "duty_evidence_uncertain",
            }
        )
    return {
        "schema_version": "deeplaw.ocr-mutation-set/v1",
        "source_free_synthetic": True,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic OCR-risk mutations")
    parser.add_argument("text")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    rendered = canonical_json(generate(arguments.text)) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
