"""Execute repository commands and collect GitLab-native result artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from qg_gitlab.api import HttpGitLab, integer
from qg_gitlab.compiler import QUALITY_EXIT_CODE, configuration, execution_graphs, graph_digest
from qg_gitlab.publication import publish_from_environment
from quality_graph_core.adapters import (
    AdapterContext,
    AdapterError,
    adapt_exit,
    adapt_junit,
    adapt_native,
    adapt_sarif,
    adapter_failure,
    read_report,
)
from quality_graph_core.graph import AdapterKind, Graph
from quality_graph_core.result import FailureKind, GitLabProvenance, Result

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from quality_graph_core.graph import Node, Profile, Step

GATE_TIMEOUT_SECONDS = 240
GATE_INTERVAL_SECONDS = 3


def execution(root: Path, flow_id: str, node_id: str) -> tuple[Graph, Graph, Node, Profile]:
    """Resolve the exact declared execution without importing the CLI."""
    graph = Graph.from_yaml((root / "qg.yaml").read_text())
    configuration(graph)
    for selected_id, _event, projected in execution_graphs(graph):
        if selected_id == flow_id:
            for node in projected.nodes:
                if node.id == node_id:
                    return graph, projected, node, graph.expanded_profiles()[node.profile]
    message = f"unknown GitLab execution {flow_id}/{node_id}"
    raise ValueError(message)


def provenance(
    graph: Graph,
    flow_id: str,
    node: Node,
    environment: Mapping[str, str],
) -> GitLabProvenance:
    """Collect native execution identities from the GitLab runner environment."""
    mr = environment.get("CI_MERGE_REQUEST_IID")
    if mr and environment.get("CI_MERGE_REQUEST_EVENT_TYPE", "detached") != "detached":
        message = "GitLab merged-results and merge-train pipelines are not supported"
        raise ValueError(message)
    return GitLabProvenance(
        environment["CI_SERVER_URL"].rstrip("/"),
        int(environment["CI_PROJECT_ID"]),
        environment["CI_COMMIT_SHA"],
        int(environment["CI_PIPELINE_ID"]),
        int(environment["CI_JOB_ID"]),
        graph_digest(graph),
        int(environment["CI_MERGE_REQUEST_PROJECT_ID"]) if mr else None,
        int(mr) if mr else None,
        flow_id,
        node.operation_id or node.id,
    )


def run_step(step: Step, root: Path, environment: Mapping[str, str]) -> bool:
    """Run one declared shell command in its explicit working directory."""
    shell = shutil.which(step.shell or "sh")
    if shell is None or step.run is None:
        message = "GitLab execution requires an available shell and a run command"
        raise ValueError(message)
    command = (shell, "-e", "-c", step.run)
    directory = root / (step.working_directory or ".")
    completed = anyio.run(
        partial(
            anyio.run_process,
            command,
            cwd=directory,
            env={**environment, **step.environment},
            stdout=None,
            stderr=None,
            check=False,
        )
    )
    return completed.returncode == 0


def collect(context: AdapterContext, node: Node, root: Path) -> Result:
    """Adapt the existing command execution without rerunning any checks."""
    if node.result.kind is AdapterKind.EXIT_CODE:
        return adapt_exit(context)
    if node.result.path is None:
        message = "structured GitLab results require a report path"
        return adapter_failure(context, AdapterError(message))
    try:
        report = read_report(root, node.result.path)
        adapters = {
            AdapterKind.NATIVE: adapt_native,
            AdapterKind.JUNIT: adapt_junit,
            AdapterKind.SARIF: adapt_sarif,
        }
        return adapters[node.result.kind](context, report)
    except (AdapterError, OSError, ValueError) as error:
        return adapter_failure(context, AdapterError(str(error)))


def execute(root: Path, flow_id: str, node_id: str) -> int:
    """Execute each step once and persist one versioned result for this job."""
    graph, _projected, node, profile = execution(root, flow_id, node_id)
    context = provenance(graph, flow_id, node, os.environ)
    output = root / ".qg/results" / flow_id / f"{node_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    environment = {
        **os.environ,
        **profile.environment,
        **node.environment,
        "QG_NODE_ID": node_id,
        "QG_FLOW_ID": flow_id,
        "QG_OPERATION_ID": node.operation_id or node_id,
        "QG_GRAPH_DIGEST": context.graph_digest,
    }
    succeeded = all(
        run_step(step, root, environment)
        for step in (*profile.setup, *(node.steps or (node.step,)))
    )
    result = collect(AdapterContext(node.id, node.title, succeeded, context), node, root)
    output.write_text(result.to_json())
    if result.failure_kind is FailureKind.QUALITY:
        return QUALITY_EXIT_CODE
    return int(result.failure_kind is not None)


def gate(root: Path, flow_id: str) -> int:
    """Hold the native pipeline until its trusted external status is installed."""
    graph = Graph.from_yaml((root / "qg.yaml").read_text())
    settings = configuration(graph)
    actor = integer(settings.get("publisher-user-id"), "publisher-user-id")
    project = int(os.environ["CI_MERGE_REQUEST_PROJECT_ID"])
    mr = int(os.environ["CI_MERGE_REQUEST_IID"])
    pipeline = int(os.environ["CI_PIPELINE_ID"])
    digest = graph_digest(graph)
    marker = f"<!-- qg:gitlab:gate:{os.environ['CI_PROJECT_ID']}:{pipeline}:{flow_id}:{digest} -->"
    deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
    with HttpGitLab(os.environ["CI_SERVER_URL"], os.environ["CI_JOB_TOKEN"], job_token=True) as api:
        while time.monotonic() < deadline:
            for note in api.pages(f"/projects/{project}/merge_requests/{mr}/notes"):
                author = note.get("author")
                body = note.get("body")
                if (
                    isinstance(author, dict)
                    and author.get("id") == actor
                    and isinstance(body, str)
                    and marker in body
                ):
                    return 0
            time.sleep(GATE_INTERVAL_SECONDS)
    sys.stderr.write("Trusted Quality Graph status was not installed before the gate deadline.\n")
    return 1


def main(arguments: Sequence[str] | None = None) -> int:
    """Dispatch the independently installed GitLab runtime."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("publish")
    for name in ("execute", "gate"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument("--flow", required=True)
        if name == "execute":
            command.add_argument("--node", required=True)
    args = parser.parse_args(arguments)
    if args.command == "publish":
        return publish_from_environment()
    if args.command == "execute":
        return execute(args.root, args.flow, args.node)
    return gate(args.root, args.flow)
