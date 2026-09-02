"""Synchronize GitHub merge requirements from the graph declaration."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import TYPE_CHECKING

from qg_github.compiler import _validate_github_graph
from qg_github.github import GitHubError

if TYPE_CHECKING:
    from qg_github.github import GitHubPort
    from quality_graph_core.graph import Graph
    from quality_graph_core.result import JsonValue

QUALITY_GRAPH_CONTEXT = "Quality Graph"


@dataclass(frozen=True)
class RequiredChecksPlan:
    """Describe one deterministic settings mutation before it is applied."""

    surface: str
    branch: str
    method: str | None
    path: str | None
    payload: JsonValue
    before: tuple[str, ...]
    after: tuple[str, ...]

    @property
    def changed(self) -> bool:
        """Return whether applying the plan would mutate GitHub."""
        return self.method is not None

    def render(self) -> str:
        """Render the planned context diff for human confirmation."""
        removed = sorted(set(self.before) - set(self.after))
        added = sorted(set(self.after) - set(self.before))
        changes = [*(f"- {item}" for item in removed), *(f"+ {item}" for item in added)]
        detail = "\n".join(changes) if changes else "  unchanged"
        return f"Required checks plan ({self.surface}, {self.branch}):\n{detail}\n"


def plan_required_checks(port: GitHubPort, graph: Graph) -> RequiredChecksPlan:
    """Read effective protection and return a preservation-safe mutation plan."""
    configuration = _validate_github_graph(graph)
    branch = configuration.default_branch
    encoded = urllib.parse.quote(branch, safe="")
    rules = _array(_request(port, "GET", f"/rules/branches/{encoded}"), "branch rules")
    sources = {
        (
            _string(rule.get("ruleset_source_type"), "ruleset source type"),
            _integer(rule.get("ruleset_id"), "ruleset id"),
        )
        for value in rules
        for rule in (_object(value, "branch rule"),)
    }
    required_sources = {
        source
        for value in rules
        for rule in (_object(value, "branch rule"),)
        for source in (
            (
                _string(rule.get("ruleset_source_type"), "ruleset source type"),
                _integer(rule.get("ruleset_id"), "ruleset id"),
            ),
        )
        if rule.get("type") == "required_status_checks"
    }
    required_repository = [source for source in required_sources if source[0] == "Repository"]
    repository = required_repository or [source for source in sources if source[0] == "Repository"]
    organization = [source for source in sources if source[0] == "Organization"]
    if len(repository) == 1:
        return _ruleset_plan(port, branch, repository[0][1], required=configuration.merge_required)
    if len(repository) > 1:
        message = "Expected exactly one applicable repository ruleset"
        raise ValueError(message)
    if organization:
        message = (
            "Branch rules are controlled by an organization ruleset; "
            "synchronize it with organization Administration write permission"
        )
        raise PermissionError(message)
    return _classic_plan(port, branch, encoded, required=configuration.merge_required)


def apply_required_checks(port: GitHubPort, plan: RequiredChecksPlan) -> None:
    """Apply a previously displayed synchronization plan."""
    if plan.method is not None and plan.path is not None:
        _request(port, plan.method, plan.path, plan.payload)


def _ruleset_plan(
    port: GitHubPort,
    branch: str,
    ruleset_id: int,
    *,
    required: bool,
) -> RequiredChecksPlan:
    path = f"/rulesets/{ruleset_id}"
    ruleset = _object(_request(port, "GET", path), "repository ruleset")
    rules = _array(ruleset.get("rules"), "repository ruleset rules")
    index = next(
        (
            position
            for position, value in enumerate(rules)
            if _object(value, "ruleset rule").get("type") == "required_status_checks"
        ),
        None,
    )
    if index is None:
        if not required:
            return RequiredChecksPlan("repository ruleset", branch, None, None, None, (), ())
        desired_rules: list[JsonValue] = list(rules)
        desired_rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": QUALITY_GRAPH_CONTEXT}],
                    "strict_required_status_checks_policy": False,
                },
            }
        )
        plan = RequiredChecksPlan(
            "repository ruleset", branch, None, None, None, (), (QUALITY_GRAPH_CONTEXT,)
        )
        return _ruleset_update(ruleset, desired_rules, path, plan)
    current_rule = _object(rules[index], "required status checks rule")
    parameters = _object(current_rule.get("parameters"), "required status checks parameters")
    checks = _array(parameters.get("required_status_checks"), "required status checks")
    before = tuple(
        _string(_object(value, "required status check").get("context"), "status context")
        for value in checks
    )
    desired_checks = [
        value
        for value in checks
        if _object(value, "required status check").get("context") != QUALITY_GRAPH_CONTEXT
    ]
    if required:
        desired_checks.append({"context": QUALITY_GRAPH_CONTEXT})
    after = tuple(
        _string(_object(value, "required status check").get("context"), "status context")
        for value in desired_checks
    )
    if checks == desired_checks:
        return RequiredChecksPlan("repository ruleset", branch, None, None, None, before, after)
    desired_rules = list(rules)
    if desired_checks:
        desired_rule = current_rule | {
            "parameters": parameters | {"required_status_checks": desired_checks}
        }
        desired_rules[index] = desired_rule
    else:
        desired_rules.pop(index)
    plan = RequiredChecksPlan("repository ruleset", branch, None, None, None, before, after)
    return _ruleset_update(ruleset, desired_rules, path, plan)


def _ruleset_update(
    ruleset: dict[str, JsonValue],
    rules: list[JsonValue],
    path: str,
    plan: RequiredChecksPlan,
) -> RequiredChecksPlan:
    payload: dict[str, JsonValue] = {
        key: ruleset[key]
        for key in ("name", "target", "enforcement", "bypass_actors", "conditions")
        if key in ruleset
    }
    payload["rules"] = rules
    return replace(plan, method="PUT", path=path, payload=payload)


def _classic_plan(
    port: GitHubPort,
    branch: str,
    encoded: str,
    *,
    required: bool,
) -> RequiredChecksPlan:
    path = f"/branches/{encoded}/protection/required_status_checks"
    status = _request(port, "GET", path)
    if status is None:
        if not required:
            return RequiredChecksPlan("classic branch protection", branch, None, None, None, (), ())
        protection = _request(port, "GET", f"/branches/{encoded}/protection")
        if protection is None:
            message = "Default branch has neither branch protection nor an applicable ruleset"
            raise ValueError(message)
        before: tuple[str, ...] = ()
        strict = False
        checks: list[JsonValue] | None = None
    else:
        value = _object(status, "required status check protection")
        strict = bool(value.get("strict", False))
        raw_checks = value.get("checks")
        checks = None if raw_checks is None else _array(raw_checks, "status checks")
        if checks is None:
            contexts = _array(value.get("contexts", []), "status contexts")
            before = tuple(_string(item, "status context") for item in contexts)
        else:
            before = tuple(
                _string(_object(item, "status check").get("context"), "status context")
                for item in checks
            )
    desired = tuple(item for item in before if item != QUALITY_GRAPH_CONTEXT)
    if required:
        desired = (*desired, QUALITY_GRAPH_CONTEXT)
    if before == desired:
        return RequiredChecksPlan(
            "classic branch protection", branch, None, None, None, before, desired
        )
    if checks is None:
        payload: JsonValue = {"strict": strict, "contexts": list(desired)}
    else:
        desired_checks = [
            item
            for item in checks
            if _object(item, "status check").get("context") != QUALITY_GRAPH_CONTEXT
        ]
        if required:
            desired_checks.append({"context": QUALITY_GRAPH_CONTEXT})
        payload = {"strict": strict, "checks": desired_checks}
    return RequiredChecksPlan(
        "classic branch protection", branch, "PATCH", path, payload, before, desired
    )


def _request(port: GitHubPort, method: str, path: str, payload: JsonValue = None) -> JsonValue:
    try:
        return port.request(method, path, payload)
    except GitHubError as error:
        if error.status_code == HTTPStatus.FORBIDDEN:
            message = "GitHub token requires repository Administration write permission"
            raise PermissionError(message) from error
        raise


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    return value
