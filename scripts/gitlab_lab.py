"""Manage the isolated real GitLab end-to-end laboratory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

    from quality_graph_core.result import JsonValue

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "tests/gitlab"
STATE = LAB / ".state"
SERVER = "http://127.0.0.1:8929"
INTERNAL_SERVER = "http://gitlab:8929"
BOOTSTRAP = (
    "u = User.find_by_username!('root'); "
    "t = u.personal_access_tokens.find_or_initialize_by(name: 'qg-lab-bootstrap'); "
    "t.scopes = ['api']; t.expires_at = 30.days.from_now; "
    "t.set_token(File.read('/run/secrets/admin_token').strip); "
    "t.save!; puts 'Quality Graph lab administration initialized'"
)


def run(arguments: Sequence[str], *, data: bytes | None = None) -> bytes:
    """Execute an argv command without exposing its captured output."""
    result = anyio.run(partial(anyio.run_process, arguments, input=data, cwd=ROOT))
    return result.stdout


def compose(*arguments: str) -> bytes:
    """Address only the configured local laboratory Docker context."""
    binary = shutil.which("docker-compose")
    if binary is None:
        message = "docker-compose is required for the real GitLab laboratory"
        raise RuntimeError(message)
    return run(
        (
            binary,
            "--context",
            os.environ.get("QG_GITLAB_DOCKER_CONTEXT", "colima-qg-gitlab"),
            "-f",
            str(LAB / "docker-compose.yml"),
            *arguments,
        ),
    )


def prepare() -> None:
    """Create local credentials only when absent."""
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("root-password", "admin-token"):
        path = STATE / name
        if not path.exists():
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as output:
                output.write(secrets.token_urlsafe(36))


class LabClient:
    """Seed disposable projects through the real GitLab REST interface."""

    def __init__(self, token_path: Path | None = None) -> None:
        """Load the lab-only bootstrap credential."""
        self.client = httpx.Client(
            base_url=f"{SERVER}/api/v4",
            headers={"PRIVATE-TOKEN": (token_path or STATE / "admin-token").read_text().strip()},
            timeout=60,
            trust_env=False,
        )

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        """Decode one successful lab API response."""
        response = self.client.request(method, path, json=data)
        response.raise_for_status()
        return cast("JsonValue", response.json())

    def group(self, path: str, parent: int | None = None) -> int:
        """Find or create one namespace below the laboratory group."""
        existing = self.request("GET", f"/groups?search={path}&per_page=100")
        if isinstance(existing, list):
            for value in existing:
                if (
                    isinstance(value, dict)
                    and value.get("path") == path
                    and value.get("parent_id") == parent
                ):
                    return identity(value)
        return identity(
            self.request(
                "POST",
                "/groups",
                {
                    "name": path,
                    "path": path,
                    "parent_id": parent,
                    "visibility": "public",
                },
            )
        )

    def project(self, path: str, namespace: int) -> int:
        """Find or create an isolated seeded project."""
        existing = self.request("GET", f"/groups/{namespace}/projects?per_page=100")
        if isinstance(existing, list):
            for value in existing:
                if isinstance(value, dict) and value.get("path") == path:
                    return identity(value)
        return identity(
            self.request(
                "POST",
                "/projects",
                {
                    "name": path,
                    "path": path,
                    "namespace_id": namespace,
                    "visibility": "public",
                    "initialize_with_readme": True,
                    "default_branch": "main",
                },
            )
        )

    def runner(self, project: int, name: str, *, protected: bool) -> None:
        """Register an isolated Docker executor for exactly one project."""
        path = STATE / f"{name}.toml"
        if not path.exists():
            result = self.request(
                "POST",
                "/user/runners",
                {
                    "runner_type": "project_type",
                    "project_id": project,
                    "description": name,
                    "tag_list": [name],
                    "run_untagged": False,
                    "access_level": "ref_protected" if protected else "not_protected",
                },
            )
            token = result.get("token") if isinstance(result, dict) else None
            if not isinstance(token, str):
                message = "GitLab did not return a runner authentication token"
                raise TypeError(message)
            source = "\n".join(
                (
                    "concurrent = 2",
                    "check_interval = 1",
                    "[[runners]]",
                    f"name = {json.dumps(name)}",
                    f"url = {json.dumps(INTERNAL_SERVER)}",
                    f"token = {json.dumps(token)}",
                    'executor = "docker"',
                    "[runners.docker]",
                    'image = "python:3.12-slim"',
                    "privileged = false",
                    'volumes = ["/cache"]',
                    'network_mode = "qg-gitlab-lab"',
                    'pull_policy = "if-not-present"',
                    "",
                )
            )
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as output:
                output.write(source)
        service = "publisher-runner" if protected else "execution-runner"
        compose("--profile", "runners", "up", "-d", service)
        compose("cp", str(path), f"{service}:/etc/gitlab-runner/config.toml")
        compose("restart", service)

    def user(self, name: str) -> int:
        """Create a local test account without email delivery."""
        users = self.request("GET", f"/users?username={name}")
        if isinstance(users, list) and users:
            return identity(users[0])
        return identity(
            self.request(
                "POST",
                "/users",
                {
                    "username": name,
                    "name": name,
                    "email": f"{name}@example.test",
                    "password": secrets.token_urlsafe(36),
                    "skip_confirmation": True,
                },
            )
        )

    def member(self, project: int, user: int, access: int) -> None:
        """Grant only the intended test-project role to an account."""
        members = self.request("GET", f"/projects/{project}/members/all")
        if isinstance(members, list) and any(
            isinstance(member, dict) and member.get("id") == user for member in members
        ):
            return
        self.request(
            "POST", f"/projects/{project}/members", {"user_id": user, "access_level": access}
        )

    def token(self, user: int, name: str) -> Path:
        """Persist a lab-only token privately instead of exposing it in logs."""
        path = STATE / name
        if not path.exists():
            result = self.request(
                "POST",
                f"/users/{user}/personal_access_tokens",
                {
                    "name": name,
                    "scopes": ["api"],
                    "expires_at": (datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
                },
            )
            token = result.get("token") if isinstance(result, dict) else None
            if not isinstance(token, str):
                message = "GitLab did not return a test authentication token"
                raise TypeError(message)
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as output:
                output.write(token)
        return path

    def variables(self, project: int, values: dict[str, str], *, protected: bool) -> None:
        """Set a project's explicit integration-test variables idempotently."""
        existing = self.request("GET", f"/projects/{project}/variables")
        names = (
            {str(item.get("key")) for item in existing if isinstance(item, dict)}
            if isinstance(existing, list)
            else set()
        )
        for name, value in values.items():
            endpoint = f"/projects/{project}/variables"
            self.request(
                "PUT" if name in names else "POST",
                f"{endpoint}/{name}" if name in names else endpoint,
                {
                    "key": name,
                    "value": value,
                    "protected": protected,
                    "masked": name == "QG_GITLAB_TOKEN",
                },
            )

    def webhook(self, consumer: int, publisher: int) -> None:
        """Connect consumer job/MR/note events to the protected publisher ref."""
        triggers = self.request("GET", f"/projects/{publisher}/triggers")
        trigger = (
            next(
                (
                    item
                    for item in triggers
                    if isinstance(item, dict) and item.get("description") == "qg-lab-probe"
                ),
                None,
            )
            if isinstance(triggers, list)
            else None
        )
        if trigger is None:
            created = self.request(
                "POST", f"/projects/{publisher}/triggers", {"description": "qg-lab-probe"}
            )
            trigger = created if isinstance(created, dict) else None
        token = trigger.get("token") if isinstance(trigger, dict) else None
        if not isinstance(token, str):
            message = "GitLab did not return a pipeline trigger token"
            raise TypeError(message)
        hooks = self.request("GET", f"/projects/{consumer}/hooks")
        if not hooks:
            self.request(
                "POST",
                f"/projects/{consumer}/hooks",
                {
                    "url": (
                        f"{INTERNAL_SERVER}/api/v4/projects/{publisher}/ref/main/trigger/pipeline"
                        f"?token={token}"
                    ),
                    "job_events": True,
                    "merge_requests_events": True,
                    "note_events": True,
                    "push_events": False,
                },
            )


def identity(value: JsonValue) -> int:
    """Require an API-generated integer identity."""
    number = value.get("id") if isinstance(value, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        message = "GitLab did not return an integer resource identity"
        raise TypeError(message)
    return number


def seed() -> None:
    """Initialize administration and nested consumer/publisher projects."""
    sys.stdout.write(compose("exec", "-T", "gitlab", "gitlab-rails", "runner", BOOTSTRAP).decode())
    client = LabClient()
    try:
        group = client.group("qg-lab")
        team = client.group("team", group)
        consumer = client.project("consumer", team)
        publisher = client.project("publisher", team)
        values = {
            "server": SERVER,
            "internal_server": INTERNAL_SERVER,
            "consumer": consumer,
            "publisher": publisher,
        }
        client.runner(consumer, "qg-execution", protected=False)
        client.runner(publisher, "qg-publisher", protected=True)
        values["publisher_user"] = seed_runtime(client, consumer, publisher)
        (STATE / "projects.json").write_text(json.dumps(values, indent=2) + "\n")
        sys.stdout.write(json.dumps(values, indent=2) + "\n")
    finally:
        client.client.close()


def seed_runtime(client: LabClient, consumer: int, publisher: int) -> int:
    """Prepare the same scoped publisher and offline installation used by the product."""
    bot = client.user("qg-publisher")
    reporter = client.user("qg-reporter")
    client.member(consumer, bot, 40)
    client.member(publisher, bot, 40)
    client.member(consumer, reporter, 20)
    client.request(
        "PUT",
        f"/projects/{publisher}",
        {
            "ci_pipeline_variables_minimum_override_role": "no_one_allowed",
        },
    )
    client.request(
        "PUT",
        f"/projects/{consumer}",
        {
            "only_allow_merge_if_pipeline_succeeds": True,
            "allow_merge_on_skipped_pipeline": False,
        },
    )
    token = client.token(bot, "publisher-token")
    client.token(reporter, "reporter-token")
    packages = {
        "PIP_FIND_LINKS": "http://wheelhouse:8080",
        "PIP_TRUSTED_HOST": "wheelhouse",
        "PIP_NO_INDEX": "true",
    }
    client.variables(consumer, packages, protected=False)
    client.variables(
        publisher,
        {
            **packages,
            "QG_GITLAB_TOKEN": token.read_text(),
            "QG_GITLAB_PROJECTS": json.dumps([consumer]),
        },
        protected=True,
    )
    client.request(
        "PUT", "/application/settings", {"allow_local_requests_from_web_hooks_and_services": True}
    )
    client.webhook(consumer, publisher)
    return bot


def main() -> int:
    """Run one explicit lab lifecycle operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("up", "seed", "status", "logs", "down", "wheels"))
    args = parser.parse_args()
    if args.operation == "wheels":
        wheels()
    elif args.operation == "up":
        prepare()
        sys.stdout.write("Starting GitLab; first initialization can take several minutes.\n")
        sys.stdout.flush()
        sys.stdout.write(compose("up", "-d", "--wait", "--wait-timeout", "900", "gitlab").decode())
    elif args.operation == "seed":
        seed()
    elif args.operation == "status":
        sys.stdout.write(compose("ps", "--all").decode())
    elif args.operation == "logs":
        sys.stdout.write(compose("logs", "--tail", "100", "gitlab").decode())
    else:
        sys.stdout.write(compose("--profile", "runners", "down").decode())
    return 0


def wheels() -> None:
    """Refresh candidate wheels and Linux dependencies for isolated CI installation."""
    destination = STATE / "wheelhouse"
    destination.mkdir(parents=True, exist_ok=True)
    for package in ("core", "gitlab", "cli"):
        candidates = tuple((ROOT / "dist").glob(f"quality_graph_{package}-*-py3-none-any.whl"))
        if len(candidates) != 1:
            message = f"Build exactly one {package} wheel before preparing the lab"
            raise ValueError(message)
        shutil.copyfile(candidates[0], destination / candidates[0].name)
    dependencies: list[str] = []
    for package in ("core", "gitlab"):
        config = tomllib.loads((ROOT / "packages" / package / "pyproject.toml").read_text())
        dependencies.extend(
            item
            for item in config["project"]["dependencies"]
            if not item.startswith("quality-graph-")
        )
    architecture = "aarch64" if platform.machine() == "arm64" else "x86_64"
    run(
        (
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(destination),
            "--only-binary=:all:",
            "--platform",
            f"manylinux2014_{architecture}",
            "--python-version",
            "312",
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            *dependencies,
        )
    )
    compose("--profile", "runners", "up", "-d", "wheelhouse")
    sys.stdout.write("Candidate wheels and Linux dependencies are available to lab jobs.\n")


if __name__ == "__main__":
    sys.exit(main())
