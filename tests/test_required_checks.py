import pytest

from qg_github.github import GitHubError, MemoryGitHubPort
from qg_github.required_checks import apply_required_checks, plan_required_checks
from quality_graph_core.graph import Graph
from quality_graph_core.result import JsonValue
from tests.test_graph import GRAPH


def graph(*, required: bool) -> Graph:
    merge = f"    merge:\n      required: {str(required).lower()}\n"
    return Graph.from_yaml(GRAPH.replace("    runtime:\n", merge + "    runtime:\n"))


def test_classic_protection_preserves_unrelated_checks_and_is_idempotent() -> None:
    port = MemoryGitHubPort()
    rules_path = "/rules/branches/main"
    protection_path = "/branches/main/protection/required_status_checks"
    port.enqueue("GET", rules_path, [], [])
    port.enqueue(
        "GET",
        protection_path,
        {"strict": True, "checks": [{"context": "external", "app_id": 7}]},
        {
            "strict": True,
            "checks": [
                {"context": "external", "app_id": 7},
                {"context": "Quality Graph"},
            ],
        },
    )
    port.enqueue(
        "PATCH",
        protection_path,
        {
            "strict": True,
            "checks": [
                {"context": "external", "app_id": 7},
                {"context": "Quality Graph"},
            ],
        },
    )

    first = plan_required_checks(port, graph(required=True))
    assert first.before == ("external",)
    assert first.after == ("external", "Quality Graph")
    assert "+ Quality Graph" in first.render()
    apply_required_checks(port, first)
    second = plan_required_checks(port, graph(required=True))

    assert second.changed is False
    assert port.requests[2] == (
        "PATCH",
        protection_path,
        {
            "strict": True,
            "checks": [
                {"context": "external", "app_id": 7},
                {"context": "Quality Graph"},
            ],
        },
    )


def test_classic_protection_removes_only_managed_context() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/rules/branches/main", [])
    path = "/branches/main/protection/required_status_checks"
    port.enqueue("GET", path, {"strict": False, "contexts": ["Quality Graph", "external"]})
    port.enqueue("PATCH", path, {"strict": False, "contexts": ["external"]})

    plan = plan_required_checks(port, graph(required=False))
    apply_required_checks(port, plan)

    assert plan.after == ("external",)
    assert port.requests[-1] == ("PATCH", path, {"strict": False, "contexts": ["external"]})


def test_classic_protection_preserves_managed_app_binding_and_order() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/rules/branches/main", [])
    path = "/branches/main/protection/required_status_checks"
    port.enqueue(
        "GET",
        path,
        {
            "strict": True,
            "checks": [
                {"context": "Quality Graph", "app_id": 7},
                {"context": "external", "app_id": 9},
            ],
        },
    )

    plan = plan_required_checks(port, graph(required=True))

    assert plan.changed is False
    assert plan.before == ("Quality Graph", "external")
    assert port.requests == [
        ("GET", "/rules/branches/main", None),
        ("GET", path, None),
    ]


def test_repository_ruleset_preserves_unrelated_settings() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    port.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    ruleset = {
        "id": 42,
        "name": "main",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [{"actor_id": 5, "actor_type": "Team", "bypass_mode": "always"}],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "required_linear_history"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": "external", "integration_id": 7}],
                },
            },
        ],
    }
    port.enqueue("GET", "/rulesets/42", ruleset)
    port.enqueue("PUT", "/rulesets/42", {})

    plan = plan_required_checks(port, graph(required=True))
    apply_required_checks(port, plan)
    payload = port.requests[-1][2]

    assert isinstance(payload, dict)
    assert payload["bypass_actors"] == ruleset["bypass_actors"]
    assert payload["conditions"] == ruleset["conditions"]
    assert payload["rules"][0] == {"type": "required_linear_history"}
    checks = payload["rules"][1]["parameters"]["required_status_checks"]
    assert checks == [{"context": "external", "integration_id": 7}, {"context": "Quality Graph"}]


def test_repository_ruleset_adds_missing_required_status_rule() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_linear_history",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    port.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    port.enqueue(
        "GET",
        "/rulesets/42",
        {
            "name": "main",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [{"type": "required_linear_history"}],
        },
    )

    plan = plan_required_checks(port, graph(required=True))

    assert plan.surface == "repository ruleset"
    assert plan.after == ("Quality Graph",)
    assert plan.changed is True


def test_repository_ruleset_without_required_rule_is_unchanged_when_disabled() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_linear_history",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    port.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    port.enqueue("GET", "/rulesets/42", {"rules": [{"type": "required_linear_history"}]})

    assert plan_required_checks(port, graph(required=False)).changed is False


def test_repository_ruleset_idempotence_and_managed_rule_removal() -> None:
    rule = {
        "type": "required_status_checks",
        "parameters": {
            "required_status_checks": [
                {"context": "Quality Graph", "integration_id": 7},
                {"context": "external", "integration_id": 9},
            ]
        },
    }
    unchanged = MemoryGitHubPort()
    unchanged.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    unchanged.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    unchanged.enqueue("GET", "/rulesets/42", {"rules": [rule]})
    assert plan_required_checks(unchanged, graph(required=True)).changed is False

    removal = MemoryGitHubPort()
    removal.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    removal.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    removal.enqueue("GET", "/rulesets/42", {"rules": [rule]})
    plan = plan_required_checks(removal, graph(required=False))
    assert plan.payload == {
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "external", "integration_id": 9}]
                },
            }
        ]
    }

    sole = MemoryGitHubPort()
    sole.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    sole.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    sole.enqueue(
        "GET",
        "/rulesets/42",
        {
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "Quality Graph"}]},
                }
            ]
        },
    )
    assert plan_required_checks(sole, graph(required=False)).payload == {"rules": []}


def test_multiple_repository_rulesets_are_rejected() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_linear_history",
                "ruleset_source_type": "Repository",
                "ruleset_id": ruleset_id,
            }
            for ruleset_id in (1, 2)
        ],
    )
    port.enqueue("GET", "/branches/main/protection/required_status_checks", None)

    with pytest.raises(ValueError, match="exactly one"):
        plan_required_checks(port, graph(required=True))


def test_classic_required_checks_take_precedence_over_unrelated_repository_ruleset() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_linear_history",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    path = "/branches/main/protection/required_status_checks"
    port.enqueue(
        "GET",
        path,
        {"strict": False, "contexts": ["Quality Graph", "external"]},
    )

    plan = plan_required_checks(port, graph(required=False))

    assert plan.surface == "classic branch protection"
    assert plan.changed is True
    assert plan.payload == {"strict": False, "contexts": ["external"]}
    assert all(request_path != "/rulesets/42" for _, request_path, _ in port.requests)


def test_classic_and_repository_required_checks_are_ambiguous() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Repository",
                "ruleset_id": 42,
            }
        ],
    )
    port.enqueue(
        "GET",
        "/branches/main/protection/required_status_checks",
        {"strict": False, "contexts": ["Quality Graph"]},
    )

    with pytest.raises(ValueError, match="both classic protection and a repository ruleset"):
        plan_required_checks(port, graph(required=False))

    assert all(request_path != "/rulesets/42" for _, request_path, _ in port.requests)


def test_classic_missing_status_protection_paths() -> None:
    disabled = MemoryGitHubPort()
    disabled.enqueue("GET", "/rules/branches/main", [])
    disabled.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    assert plan_required_checks(disabled, graph(required=False)).changed is False

    enabled = MemoryGitHubPort()
    enabled.enqueue("GET", "/rules/branches/main", [])
    enabled.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    enabled.enqueue(
        "GET",
        "/branches/main/protection",
        {
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": 2,
                "require_last_push_approval": True,
                "dismissal_restrictions": {
                    "users": [{"login": "maintainer"}],
                    "teams": [{"slug": "reviewers"}],
                    "apps": [{"slug": "review-app"}],
                },
            },
            "restrictions": {
                "users": [{"login": "deployer"}],
                "teams": [{"slug": "release"}],
                "apps": [{"slug": "deploy-app"}],
            },
            "required_linear_history": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
        },
    )
    plan = plan_required_checks(enabled, graph(required=True))
    assert plan.after == ("Quality Graph",)
    assert plan.method == "PUT"
    assert plan.path == "/branches/main/protection"
    assert plan.payload == {
        "required_status_checks": {"strict": False, "contexts": ["Quality Graph"]},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 2,
            "require_last_push_approval": True,
            "dismissal_restrictions": {
                "users": ["maintainer"],
                "teams": ["reviewers"],
                "apps": ["review-app"],
            },
        },
        "restrictions": {
            "users": ["deployer"],
            "teams": ["release"],
            "apps": ["deploy-app"],
        },
        "required_linear_history": True,
        "allow_force_pushes": False,
    }

    unprotected = MemoryGitHubPort()
    unprotected.enqueue("GET", "/rules/branches/main", [])
    unprotected.enqueue("GET", "/branches/main/protection/required_status_checks", None)
    unprotected.enqueue("GET", "/branches/main/protection", None)
    with pytest.raises(ValueError, match="neither branch protection"):
        plan_required_checks(unprotected, graph(required=True))


def test_classic_full_protection_preserves_disabled_settings_and_validates_flags() -> None:
    path = "/branches/main/protection/required_status_checks"
    disabled = MemoryGitHubPort()
    disabled.enqueue("GET", "/rules/branches/main", [])
    disabled.enqueue("GET", path, None)
    disabled.enqueue(
        "GET",
        "/branches/main/protection",
        {
            "enforce_admins": None,
            "required_pull_request_reviews": None,
            "restrictions": None,
        },
    )

    plan = plan_required_checks(disabled, graph(required=True))

    assert plan.payload == {
        "required_status_checks": {"strict": False, "contexts": ["Quality Graph"]},
        "enforce_admins": None,
        "required_pull_request_reviews": None,
        "restrictions": None,
    }

    malformed = MemoryGitHubPort()
    malformed.enqueue("GET", "/rules/branches/main", [])
    malformed.enqueue("GET", path, None)
    malformed.enqueue(
        "GET",
        "/branches/main/protection",
        {"required_linear_history": {"enabled": "yes"}},
    )
    with pytest.raises(TypeError, match="enabled must be a boolean"):
        plan_required_checks(malformed, graph(required=True))


def test_organization_ruleset_requires_organization_administration() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": "Organization",
                "ruleset_id": 73,
            }
        ],
    )

    with pytest.raises(PermissionError, match="organization Administration"):
        plan_required_checks(port, graph(required=True))


def test_organization_required_rule_blocks_repository_ruleset_mutation() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/rules/branches/main",
        [
            {
                "type": "required_status_checks",
                "ruleset_source_type": source_type,
                "ruleset_id": ruleset_id,
            }
            for source_type, ruleset_id in (("Organization", 73), ("Repository", 42))
        ],
    )

    with pytest.raises(PermissionError, match="organization ruleset"):
        plan_required_checks(port, graph(required=True))

    assert all(path != "/rulesets/42" for _, path, _ in port.requests)


def test_insufficient_repository_permission_is_actionable() -> None:
    class ForbiddenPort(MemoryGitHubPort):
        def request(self, method: str, path: str, payload: JsonValue = None) -> JsonValue:
            del payload
            raise GitHubError(method, path, 403)

    with pytest.raises(PermissionError, match="repository Administration write"):
        plan_required_checks(ForbiddenPort(), graph(required=True))

    class InvalidPort(MemoryGitHubPort):
        def request(self, method: str, path: str, payload: JsonValue = None) -> JsonValue:
            del payload
            raise GitHubError(method, path, 500)

    with pytest.raises(GitHubError, match="HTTP 500"):
        plan_required_checks(InvalidPort(), graph(required=True))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({}, "branch rules must be an array"),
        ([None], "branch rule must be an object"),
        ([{"type": "rule", "ruleset_source_type": 1, "ruleset_id": 1}], "source type"),
        ([{"type": "rule", "ruleset_source_type": "Repository", "ruleset_id": True}], "ruleset id"),
    ],
)
def test_malformed_rule_discovery_is_rejected(response: JsonValue, message: str) -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/rules/branches/main", response)

    with pytest.raises(TypeError, match=message):
        plan_required_checks(port, graph(required=True))
