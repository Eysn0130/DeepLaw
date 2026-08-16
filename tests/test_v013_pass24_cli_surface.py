from __future__ import annotations

import argparse

from deeplaw import cli


def _knowledge_parser() -> argparse.ArgumentParser:
    parser = cli._parser()
    root = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return root.choices["knowledge"]


def _knowledge_subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    return next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def test_default_help_highlights_the_existing_query_context_journey() -> None:
    parser = _knowledge_parser()
    help_text = parser.format_help()
    for command in (
        "init",
        "doctor",
        "source",
        "compile",
        "reconcile",
        "query",
        "context",
        "snapshot",
        "forget",
        "host",
        "task",
    ):
        assert command in help_text

    visible = [
        choice.dest for choice in _knowledge_subparsers(parser)._choices_actions
    ]
    assert visible == [
        "init",
        "doctor",
        "source",
        "compile",
        "reconcile",
        "query",
        "context",
        "wiki",
        "snapshot",
        "forget",
        "host",
        "task",
    ]
    for hidden in (
        "semantic",
        "synthesis",
        "backfill",
        "diagnose-retrieval",
        "retrieval-profile",
        "discovery-model",
        "sink",
    ):
        assert hidden not in help_text


def test_query_and_context_parse_through_existing_public_seams() -> None:
    parser = cli._parser()
    query = parser.parse_args(
        [
            "knowledge",
            "query",
            "--vault",
            "vault",
            "--query",
            "preserve the source boundary",
        ]
    )
    assert query.knowledge_command == "query"
    assert query.query == "preserve the source boundary"

    context = parser.parse_args(
        [
            "knowledge",
            "context",
            "--vault",
            "vault",
            "--task",
            "continue the bounded task",
        ]
    )
    assert context.knowledge_command == "context"
    assert context.task == "continue the bounded task"


def test_hidden_commands_remain_directly_parseable() -> None:
    parser = cli._parser()
    semantic = parser.parse_args(
        ["knowledge", "semantic", "profile", "--vault", "vault"]
    )
    assert semantic.knowledge_command == "semantic"

    sink = parser.parse_args(
        [
            "knowledge",
            "sink",
            "status",
            "--vault",
            "vault",
            "--grant-id",
            "grant-test",
        ]
    )
    assert sink.knowledge_command == "sink"
