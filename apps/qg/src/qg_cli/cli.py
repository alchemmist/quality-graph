"""Command-line interface for Quality Graph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from qg_cli import __version__
from qg_cli.project import Project
from quality_graph_core.result import FailureKind, Metric, Provenance, Result, ResultStatus
from quality_graph_core.schema import graph_schema_json, result_schema_json

if TYPE_CHECKING:
    from collections.abc import Sequence


def parser() -> argparse.ArgumentParser:
    """Build the public command-line parser."""
    result = argparse.ArgumentParser(prog="qg", description="Quality Graph")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command")
    initialize = commands.add_parser("init", help="Create a starter Quality Graph declaration")
    initialize.add_argument("--root", default=".")
    initialize.add_argument("--runtime-action", required=True)
    initialize.add_argument("--preset", choices=("oss", "internal"), default="oss")
    initialize.add_argument("--force", action="store_true")
    generate = commands.add_parser("generate", help="Generate committed GitHub workflows")
    generate.add_argument("--root", default=".")
    validate_project = commands.add_parser("validate", help="Validate declaration freshness")
    validate_project.add_argument("--root", default=".")
    graph_schema = commands.add_parser("schema", help="Render the graph JSON Schema")
    graph_schema.add_argument("--output", default="-")
    result_command = commands.add_parser("result", help="Work with native result JSON")
    result_commands = result_command.add_subparsers(dest="result_command")
    validate = result_commands.add_parser("validate", help="Validate native result JSON")
    validate.add_argument("path", help="JSON file path or - for stdin")
    schema = result_commands.add_parser("schema", help="Render the result JSON Schema")
    schema.add_argument("--output", default="-", help="Output path or - for stdout")
    emit = result_commands.add_parser("emit", help="Emit a minimal native result")
    emit.add_argument("--node-id", required=True)
    emit.add_argument("--title", required=True)
    emit.add_argument("--status", choices=tuple(ResultStatus), required=True)
    emit.add_argument("--failure-kind", choices=tuple(FailureKind))
    emit.add_argument("--summary", default="")
    emit.add_argument("--metric", action="append", default=[])
    emit.add_argument("--repository", required=True)
    emit.add_argument("--pull-request", type=int)
    emit.add_argument("--head-sha", required=True)
    emit.add_argument("--workflow-run-id", type=int, required=True)
    emit.add_argument("--run-attempt", type=int, required=True)
    emit.add_argument("--graph-digest", required=True)
    emit.add_argument("--output", default="-")
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the Quality Graph command-line interface."""
    command_parser = parser()
    args = command_parser.parse_args(arguments)
    try:
        if args.command in {"init", "generate", "validate", "schema"}:
            return _project_command(args)
        if args.command == "result":
            return _result_command(command_parser, args)
    except (OSError, TypeError, ValueError) as error:
        command_parser.error(str(error))
    command_parser.print_help()
    return 0


def _project_command(args: argparse.Namespace) -> int:
    if args.command == "init":
        Project.initialize(
            Path(args.root),
            args.runtime_action,
            preset=args.preset,
            force=args.force,
        )
        return 0
    if args.command == "generate":
        Project.open(Path(args.root)).generate()
        return 0
    if args.command == "schema":
        _write_text(args.output, graph_schema_json())
        return 0
    report = Project.open(Path(args.root)).validate()
    if report.current:
        return 0
    sys.stderr.write("\n".join(report.problems) + "\n")
    return 1


def _result_command(command_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.result_command == "validate":
        Result.from_json(_read_text(args.path))
        return 0
    if args.result_command == "schema":
        _write_text(args.output, result_schema_json())
        return 0
    if args.result_command == "emit":
        _write_text(args.output, _emitted_result(args).to_json())
        return 0
    command_parser.print_help()
    return 0


def _emitted_result(args: argparse.Namespace) -> Result:
    metrics = tuple(_metric(raw) for raw in args.metric)
    failure = FailureKind(args.failure_kind) if args.failure_kind is not None else None
    return Result(
        args.node_id,
        args.title,
        ResultStatus(args.status),
        Provenance(
            args.repository,
            args.head_sha,
            args.workflow_run_id,
            args.run_attempt,
            args.graph_digest,
            args.pull_request,
        ),
        failure,
        args.summary,
        metrics,
    )


def _metric(value: str) -> Metric:
    label, separator, metric_value = value.partition("=")
    if not separator:
        message = "--metric must use label=value"
        raise ValueError(message)
    return Metric(label, metric_value)


def _read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text()


def _write_text(path: str, value: str) -> None:
    if path == "-":
        sys.stdout.write(value)
    else:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(value)
