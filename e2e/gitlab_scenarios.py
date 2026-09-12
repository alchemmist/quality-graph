from __future__ import annotations

import json
import secrets
import shlex
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest
import yaml
from scripts.gitlab_lab import INTERNAL_SERVER, STATE, LabClient

from qg_gitlab.compiler import RUNTIME_VERSION, compile_graph, starter
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from collections.abc import Iterator

    from quality_graph_core.result import JsonValue

TIMEOUT = 240


@dataclass(frozen=True)
class Case:
    project: int
    mr: int
    branch: str
    sha: str


def obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def items(value: JsonValue) -> list[dict[str, JsonValue]]:
    assert isinstance(value, list)
    return [obj(item) for item in value]


@pytest.fixture(scope="module")
def lab() -> Iterator[LabClient]:
    assert (STATE / "projects.json").is_file(), "Run make gitlab-seed first"
    client = LabClient()
    try:
        yield client
    finally:
        client.client.close()


def commit(lab: LabClient, project: int, branch: str, files: dict[str, str]) -> str:
    tree = items(
        lab.request(
            "GET", f"/projects/{project}/repository/tree?ref={branch}&recursive=true&per_page=100"
        )
    )
    paths = {item["path"] for item in tree}
    actions: list[JsonValue] = [
        {"action": "update" if path in paths else "create", "file_path": path, "content": body}
        for path, body in files.items()
    ]
    result = obj(
        lab.request(
            "POST",
            f"/projects/{project}/repository/commits",
            {
                "branch": branch,
                "commit_message": "exercise the installed GitLab provider",
                "actions": actions,
            },
        )
    )
    return str(result["id"])


def begin(lab: LabClient, command: str, *, adapter: str | None = None) -> Case:
    projects = json.loads((STATE / "projects.json").read_text())
    consumer = int(projects["consumer"])
    publisher = int(projects["publisher"])
    for state in ("running", "pending"):
        for pipeline in items(
            lab.request("GET", f"/projects/{consumer}/pipelines?status={state}&per_page=100")
        ):
            lab.request("POST", f"/projects/{consumer}/pipelines/{pipeline['id']}/cancel")
    for mr in items(lab.request("GET", f"/projects/{consumer}/merge_requests?state=opened")):
        lab.request(
            "PUT", f"/projects/{consumer}/merge_requests/{mr['iid']}", {"state_event": "close"}
        )
    declaration = yaml.safe_load(starter("main"))
    declaration["provider"]["configuration"].update(
        {"server-url": INTERNAL_SERVER, "publisher-user-id": projects["publisher_user"]}
    )
    node: dict[str, JsonValue] = {
        "title": "Verification",
        "run": command,
        "policy": {"approvals": {"findings": True, "files": True, "node": True}},
    }
    if adapter is not None:
        node["results"] = {adapter: "report.xml" if adapter == "junit" else "report.json"}
    declaration["nodes"] = {"verify": node}
    source = yaml.safe_dump(declaration, sort_keys=False)
    generated = compile_graph(Graph.from_yaml(source))
    commit(lab, publisher, "main", {".gitlab-ci.yml": generated.files[2].content})
    commit(
        lab,
        consumer,
        "main",
        {"qg.yaml": source, **{str(file.path): file.content for file in generated.files}},
    )
    branch = "qg-e2e-" + secrets.token_hex(4)
    lab.request(
        "POST", f"/projects/{consumer}/repository/branches", {"branch": branch, "ref": "main"}
    )
    mr = obj(
        lab.request(
            "POST",
            f"/projects/{consumer}/merge_requests",
            {
                "source_branch": branch,
                "target_branch": "main",
                "title": "Quality Graph local end-to-end",
            },
        )
    )
    sha = commit(lab, consumer, branch, {"e2e-input.txt": branch})
    return Case(consumer, int(str(mr["iid"])), branch, sha)


def head(lab: LabClient, case: Case) -> dict[str, JsonValue]:
    mr = obj(lab.request("GET", f"/projects/{case.project}/merge_requests/{case.mr}"))
    value = mr.get("head_pipeline")
    return obj(value) if isinstance(value, dict) and value.get("sha") == case.sha else {}


def summary(lab: LabClient, case: Case) -> str:
    notes = items(
        lab.request("GET", f"/projects/{case.project}/merge_requests/{case.mr}/notes?per_page=100")
    )
    matched = [
        str(note["body"])
        for note in notes
        if "<!-- quality-graph:gitlab:summary -->" in str(note.get("body"))
    ]
    assert len(matched) <= 1
    return matched[0] if matched else ""


def wait_state(lab: LabClient, case: Case, status: str) -> int:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        pipeline = head(lab, case)
        if pipeline and pipeline.get("status") == status and f"**{status}**" in summary(lab, case):
            return int(str(pipeline["id"]))
        time.sleep(1)
    pytest.fail(
        f"MR {case.mr} did not converge to {status}; "
        f"head={head(lab, case)} summary={summary(lab, case)}"
    )


def jobs(lab: LabClient, case: Case, pipeline: int) -> list[dict[str, JsonValue]]:
    return items(
        lab.request(
            "GET",
            f"/projects/{case.project}/pipelines/{pipeline}/jobs?include_retried=true&per_page=100",
        )
    )


def command(lab: LabClient, case: Case, body: str) -> None:
    note = obj(
        lab.request(
            "POST", f"/projects/{case.project}/merge_requests/{case.mr}/notes", {"body": body}
        )
    )
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        notes = items(
            lab.request(
                "GET", f"/projects/{case.project}/merge_requests/{case.mr}/notes?per_page=100"
            )
        )
        if any(f'"sourceNoteId":{note["id"]},' in str(item.get("body")) for item in notes):
            return
        time.sleep(1)
    pytest.fail(f"Command {note['id']} was not acknowledged")


def test_installed_runtime_passes_and_retries_with_an_admission_barrier(lab: LabClient) -> None:
    code = "import os; assert 'QG_GITLAB_TOKEN' not in os.environ; print('check executed')"
    case = begin(lab, shlex.join(("python", "-c", code)))
    pipeline = wait_state(lab, case, "success")
    previous = next(job for job in jobs(lab, case, pipeline) if job["name"] == "qg:mr:verify")
    retry = obj(lab.request("POST", f"/projects/{case.project}/jobs/{previous['id']}/retry"))
    assert retry["id"] != previous["id"]
    assert wait_state(lab, case, "success") == pipeline
    final = jobs(lab, case, pipeline)
    assert len([job for job in final if job["name"] == "qg:mr:verify"]) == 2
    assert f"/jobs/{retry['id']}" in summary(lab, case)


def test_quality_approvals_are_reversible_without_reexecuting_checks(lab: LabClient) -> None:
    report = (
        '<testsuite tests="1" failures="1"><testcase name="broken" classname="example" '
        'file="tests/example.py"><failure message="Expected failure">details</failure>'
        "</testcase></testsuite>"
    )
    code = (
        f"from pathlib import Path; Path('report.xml').write_text({report!r}); raise SystemExit(1)"
    )
    case = begin(lab, shlex.join(("python", "-c", code)), adapter="junit")
    pipeline = wait_state(lab, case, "failed")
    before = {job["id"] for job in jobs(lab, case, pipeline)}
    command(lab, case, "/qg ignore verify")
    assert wait_state(lab, case, "success") == pipeline
    assert {job["id"] for job in jobs(lab, case, pipeline)} == before
    command(lab, case, "/qg remove-ignore verify")
    assert wait_state(lab, case, "failed") == pipeline
    assert {job["id"] for job in jobs(lab, case, pipeline)} == before
    reporter = LabClient(STATE / "reporter-token")
    try:
        command(reporter, case, "/qg ignore verify")
    finally:
        reporter.client.close()

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        notes = items(
            lab.request(
                "GET", f"/projects/{case.project}/merge_requests/{case.mr}/notes?per_page=100"
            )
        )
        if any(
            "requires a configured Quality Graph administrator role" in str(note.get("body"))
            for note in notes
        ):
            break
        time.sleep(1)
    else:
        pytest.fail("Unauthorized command was not acknowledged")
    assert wait_state(lab, case, "failed") == pipeline


def test_publisher_trigger_cannot_override_protected_configuration(lab: LabClient) -> None:
    projects = json.loads((STATE / "projects.json").read_text())
    publisher = int(projects["publisher"])
    project = obj(lab.request("GET", f"/projects/{publisher}"))
    assert project["ci_pipeline_variables_minimum_override_role"] == "no_one_allowed"
    trigger = items(lab.request("GET", f"/projects/{publisher}/triggers"))[0]
    response = lab.client.post(
        f"/projects/{publisher}/trigger/pipeline",
        json={
            "token": trigger["token"],
            "ref": "main",
            "variables": {"QG_GITLAB_PROJECTS": "[999999]"},
        },
    )
    assert response.status_code == 400


def test_erased_current_artifact_cannot_keep_a_successful_graph(lab: LabClient) -> None:
    case = begin(lab, "python -c 'print(42)'")
    pipeline = wait_state(lab, case, "success")
    before = jobs(lab, case, pipeline)
    worker = next(job for job in before if job["name"] == "qg:mr:verify")
    lab.request("POST", f"/projects/{case.project}/jobs/{worker['id']}/erase")
    projects = json.loads((STATE / "projects.json").read_text())
    publisher = int(projects["publisher"])
    trigger = items(lab.request("GET", f"/projects/{publisher}/triggers"))[0]
    lab.request(
        "POST",
        f"/projects/{publisher}/trigger/pipeline",
        {"token": trigger["token"], "ref": "main"},
    )
    assert wait_state(lab, case, "failed") == pipeline
    assert {job["id"] for job in jobs(lab, case, pipeline)} == {job["id"] for job in before}


def test_explicit_native_skip_is_preserved(lab: LabClient) -> None:
    command = (
        f"python -m pip install --disable-pip-version-check quality-graph-cli=={RUNTIME_VERSION}\n"
        'if test -n "$CI_MERGE_REQUEST_IID"; then\n'
        '  set -- --target-project-id "$CI_MERGE_REQUEST_PROJECT_ID" '
        '--merge-request "$CI_MERGE_REQUEST_IID"\n'
        "else\n  set --\nfi\n"
        'qg result emit --provider gitlab --node-id "$QG_NODE_ID" --title "$QG_NODE_TITLE" '
        '--status skipped --server-url "$CI_SERVER_URL" --project-id "$CI_PROJECT_ID" '
        '--pipeline-id "$CI_PIPELINE_ID" --job-id "$CI_JOB_ID" --head-sha "$CI_COMMIT_SHA" '
        '--graph-digest "$QG_GRAPH_DIGEST" --flow-id "$QG_FLOW_ID" '
        '--operation-id "$QG_OPERATION_ID" "$@" --output report.json'
    )
    case = begin(lab, command, adapter="native")
    wait_state(lab, case, "success")
    assert "skipped" in summary(lab, case)


def test_command_failures_cannot_be_approved(lab: LabClient) -> None:
    case = begin(lab, "python -c 'raise SystemExit(1)'")
    pipeline = wait_state(lab, case, "failed")
    command(lab, case, "/qg ignore verify")
    assert wait_state(lab, case, "failed") == pipeline


def test_new_head_replaces_the_published_pipeline(lab: LabClient) -> None:
    case = begin(lab, "python -c 'print(42)'")
    previous = wait_state(lab, case, "success")
    sha = commit(lab, case.project, case.branch, {"e2e-input.txt": "new head"})
    current = replace(case, sha=sha)
    pipeline = wait_state(lab, current, "success")
    assert pipeline != previous
    assert f"Pipeline **{pipeline}**" in summary(lab, current)
    assert f"Pipeline **{previous}**" not in summary(lab, current)


def test_canceling_a_real_execution_never_publishes_success(lab: LabClient) -> None:
    case = begin(lab, "python -c 'import time; time.sleep(120)'")
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        pipeline = head(lab, case)
        if pipeline and pipeline.get("status") == "running":
            break
        time.sleep(1)
    else:
        pytest.fail("The cancellation scenario did not start")
    lab.request("POST", f"/projects/{case.project}/pipelines/{pipeline['id']}/cancel")
    assert wait_state(lab, case, "canceled") == int(str(pipeline["id"]))


def test_fork_mr_runs_safely_in_the_target_project(lab: LabClient) -> None:
    initial = begin(
        lab, "python -c 'import os; assert \"QG_GITLAB_TOKEN\" not in os.environ; print(42)'"
    )
    wait_state(lab, initial, "success")
    lab.request(
        "PUT", f"/projects/{initial.project}/merge_requests/{initial.mr}", {"state_event": "close"}
    )
    reporter = LabClient(STATE / "reporter-token")
    try:
        name = "qg-fork-" + secrets.token_hex(4)
        fork = obj(
            reporter.request(
                "POST", f"/projects/{initial.project}/fork", {"name": name, "path": name}
            )
        )
        fork_id = int(str(fork["id"]))
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            metadata = obj(reporter.request("GET", f"/projects/{fork_id}"))
            if metadata.get("import_status") == "finished":
                break
            time.sleep(1)
        else:
            pytest.fail("The local fork did not finish importing")
        branch = "fork-check"
        reporter.request(
            "POST", f"/projects/{fork_id}/repository/branches", {"branch": branch, "ref": "main"}
        )
        sha = commit(reporter, fork_id, branch, {"fork-input.txt": "untrusted fork"})
        mr = obj(
            reporter.request(
                "POST",
                f"/projects/{fork_id}/merge_requests",
                {
                    "source_branch": branch,
                    "target_branch": "main",
                    "target_project_id": initial.project,
                    "title": "Quality Graph untrusted fork end-to-end",
                },
            )
        )
        case = Case(initial.project, int(str(mr["iid"])), branch, sha)
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            prepared = obj(
                lab.request("GET", f"/projects/{initial.project}/merge_requests/{case.mr}")
            )
            refs = prepared.get("diff_refs")
            if isinstance(refs, dict) and refs.get("head_sha") == sha:
                break
            time.sleep(1)
        else:
            pytest.fail("GitLab did not prepare the fork MR refs")
        pipeline = obj(
            lab.request("POST", f"/projects/{initial.project}/merge_requests/{case.mr}/pipelines")
        )
        assert wait_state(lab, case, "success") == int(str(pipeline["id"]))
        assert head(lab, case)["project_id"] == initial.project
    finally:
        reporter.client.close()
