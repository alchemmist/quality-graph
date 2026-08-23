"""Generate published JSON Schemas from protocol invariants."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from quality_graph.result import JsonValue


def _object_schema(
    properties: dict[str, JsonValue],
    required: tuple[str, ...],
) -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _string_schema(
    *,
    minimum: int = 0,
    maximum: int | None = None,
    pattern: str | None = None,
    enum: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"type": "string", "minLength": minimum}
    if maximum is not None:
        result["maxLength"] = maximum
    if pattern is not None:
        result["pattern"] = pattern
    if enum:
        result["enum"] = list(enum)
    return result


def _array_schema(item: JsonValue, maximum: int) -> dict[str, JsonValue]:
    return {"type": "array", "items": item, "maxItems": maximum}


def result_schema_value() -> dict[str, JsonValue]:
    """Return the provisional result protocol JSON Schema."""
    source_location = _object_schema(
        {
            "path": _string_schema(
                minimum=1,
                maximum=4_096,
                pattern=r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\\]+$",
            ),
            "startLine": {"type": "integer", "minimum": 1},
            "endLine": {"type": "integer", "minimum": 1},
            "startColumn": {"type": "integer", "minimum": 1},
            "endColumn": {"type": "integer", "minimum": 1},
        },
        ("path", "startLine", "endLine"),
    )
    metric = _object_schema(
        {
            "label": _string_schema(minimum=1, maximum=100),
            "value": _string_schema(maximum=500),
        },
        ("label", "value"),
    )
    finding = _object_schema(
        {
            "id": _string_schema(
                minimum=1,
                maximum=255,
                pattern=r"^[a-z0-9][a-z0-9._:-]{0,254}$",
            ),
            "severity": _string_schema(enum=("notice", "warning", "error")),
            "message": _string_schema(minimum=1, maximum=1_000),
            "ruleId": _string_schema(maximum=255),
            "documentationUrl": {"type": "string", "format": "uri"},
            "fingerprint": _string_schema(maximum=512),
            "location": {"$ref": "#/$defs/sourceLocation"},
            "group": _string_schema(maximum=255),
        },
        ("id", "severity", "message"),
    )
    annotation = _object_schema(
        {
            "level": _string_schema(enum=("notice", "warning", "error")),
            "message": _string_schema(minimum=1, maximum=1_000),
            "location": {"$ref": "#/$defs/sourceLocation"},
            "title": _string_schema(maximum=255),
        },
        ("level", "message", "location"),
    )
    diagnostic = _object_schema(
        {
            "kind": _string_schema(enum=("command", "adapter", "protocol", "infrastructure")),
            "message": _string_schema(minimum=1, maximum=1_000),
            "detail": _string_schema(maximum=20_000),
        },
        ("kind", "message", "detail"),
    )
    control = _object_schema(
        {
            "kind": _string_schema(enum=("finding", "file", "node")),
            "target": _string_schema(minimum=1, maximum=4_096),
            "checked": {"type": "boolean"},
        },
        ("kind", "target", "checked"),
    )
    provenance = _object_schema(
        {
            "repository": _string_schema(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
            "pullRequest": {"type": "integer", "minimum": 1},
            "headSha": _string_schema(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$"),
            "workflowRunId": {"type": "integer", "minimum": 0},
            "runAttempt": {"type": "integer", "minimum": 1},
            "graphDigest": _string_schema(pattern=r"^[0-9a-f]{64}$"),
        },
        ("repository", "headSha", "workflowRunId", "runAttempt", "graphDigest"),
    )
    properties: dict[str, JsonValue] = {
        "schemaVersion": {"const": 0},
        "nodeId": _string_schema(pattern=r"^[a-z][a-z0-9-]{0,62}$"),
        "title": _string_schema(minimum=1, maximum=255),
        "status": _string_schema(
            enum=("waiting", "in_progress", "passed", "failed", "skipped", "cancelled")
        ),
        "failureKind": _string_schema(
            enum=("quality", "command", "adapter", "protocol", "cancellation", "infrastructure")
        ),
        "summary": _string_schema(maximum=200_000),
        "metrics": _array_schema({"$ref": "#/$defs/metric"}, 100),
        "findings": _array_schema({"$ref": "#/$defs/finding"}, 10_000),
        "annotations": _array_schema({"$ref": "#/$defs/annotation"}, 10_000),
        "diagnostics": _array_schema({"$ref": "#/$defs/diagnostic"}, 100),
        "controls": _array_schema({"$ref": "#/$defs/control"}, 10_000),
        "notes": _array_schema(_string_schema(minimum=1, maximum=1_000), 100),
        "provenance": {"$ref": "#/$defs/provenance"},
    }
    schema = _object_schema(
        properties,
        (
            "schemaVersion",
            "nodeId",
            "title",
            "status",
            "summary",
            "metrics",
            "findings",
            "annotations",
            "diagnostics",
            "controls",
            "notes",
            "provenance",
        ),
    )
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/alchemmist/quality-graph/blob/main/schemas/result-v0.schema.json",
            "title": "Quality Graph Result v0",
            "$defs": {
                "sourceLocation": source_location,
                "metric": metric,
                "finding": finding,
                "annotation": annotation,
                "diagnostic": diagnostic,
                "control": control,
                "provenance": provenance,
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "status": {"enum": ["failed", "cancelled"]},
                        },
                        "required": ["status"],
                    },
                    "then": {"required": ["failureKind"]},
                    "else": {"not": {"required": ["failureKind"]}},
                }
            ],
        }
    )
    return schema


def result_schema_json() -> str:
    """Serialize the result JSON Schema deterministically."""
    return json.dumps(result_schema_value(), indent=2, sort_keys=True) + "\n"


def graph_schema_value() -> dict[str, JsonValue]:
    """Return the provisional graph declaration JSON Schema."""
    identifier = _string_schema(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    string_mapping: dict[str, JsonValue] = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }
    step = _object_schema(
        {
            "name": _string_schema(minimum=1),
            "run": _string_schema(minimum=1),
            "uses": _string_schema(minimum=1),
            "with": string_mapping,
            "env": string_mapping,
            "working-directory": _string_schema(minimum=1),
            "shell": _string_schema(minimum=1),
        },
        (),
    )
    step["oneOf"] = [
        {"required": ["run"], "not": {"required": ["uses"]}},
        {"required": ["uses"], "not": {"required": ["run"]}},
    ]
    profile = _object_schema(
        {
            "extends": identifier,
            "runner": _string_schema(minimum=1),
            "setup": _array_schema({"$ref": "#/$defs/step"}, 100),
            "env": string_mapping,
            "permissions": {
                "type": "object",
                "additionalProperties": {"enum": ["none", "read"]},
            },
            "timeout-minutes": {"type": "integer", "minimum": 1, "maximum": 360},
            "container": _string_schema(minimum=1),
            "services": {"type": "object"},
        },
        (),
    )
    result_adapters: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "exit-code": {"type": "null"},
            "native": _string_schema(minimum=1),
            "sarif": _string_schema(minimum=1),
            "junit": _string_schema(minimum=1),
        },
        "additionalProperties": False,
        "minProperties": 1,
        "maxProperties": 1,
    }
    approvals = _object_schema(
        {
            "findings": {"type": "boolean"},
            "files": {"type": "boolean"},
            "node": {"type": "boolean"},
        },
        (),
    )
    policy = _object_schema(
        {
            "blocking": {"type": "boolean"},
            "blocking-severities": _array_schema(
                {"enum": ["notice", "warning", "error"]},
                3,
            ),
            "approvals": approvals,
        },
        (),
    )
    label = _object_schema(
        {
            "name": _string_schema(minimum=1, maximum=50),
            "color": _string_schema(pattern=r"^[0-9a-fA-F]{6}$"),
            "description": _string_schema(maximum=100),
            "create": {"type": "boolean"},
        },
        ("name",),
    )
    node_properties = dict(cast("dict[str, JsonValue]", step["properties"]))
    node_properties.update(
        {
            "title": _string_schema(minimum=1, maximum=255),
            "profile": identifier,
            "needs": _array_schema(identifier, 1_000),
            "results": result_adapters,
            "policy": policy,
            "label": {
                "oneOf": [
                    {"type": "boolean"},
                    {"type": "string", "minLength": 1, "maxLength": 50},
                    {"$ref": "#/$defs/label"},
                ]
            },
            "timeout-minutes": {"type": "integer", "minimum": 1, "maximum": 360},
        }
    )
    node = _object_schema(node_properties, ())
    node["oneOf"] = step["oneOf"]
    labels = _object_schema(
        {
            "enabled": {"type": "boolean"},
            "failing": {
                "oneOf": [
                    {"type": "string", "minLength": 1, "maxLength": 50},
                    {"$ref": "#/$defs/label"},
                ]
            },
        },
        (),
    )
    labels["if"] = {"properties": {"enabled": {"const": True}}}
    labels["then"] = {"required": ["failing"]}
    schema = _object_schema(
        {
            "version": {"const": 0},
            "runtime": _object_schema(
                {
                    "action": _string_schema(
                        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$"
                    )
                },
                ("action",),
            ),
            "profiles": {
                "type": "object",
                "propertyNames": identifier,
                "additionalProperties": {"$ref": "#/$defs/profile"},
                "required": ["default"],
            },
            "nodes": {
                "type": "object",
                "propertyNames": identifier,
                "additionalProperties": {"$ref": "#/$defs/node"},
                "minProperties": 1,
            },
            "labels": labels,
            "administration": _object_schema(
                {
                    "roles": _array_schema(
                        {"enum": ["admin", "maintain", "write"]},
                        3,
                    )
                },
                (),
            ),
        },
        ("version", "runtime", "profiles", "nodes"),
    )
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/alchemmist/quality-graph/blob/main/schemas/graph-v0.schema.json",
            "title": "Quality Graph Declaration v0",
            "$defs": {"step": step, "profile": profile, "label": label, "node": node},
        }
    )
    return schema


def graph_schema_json() -> str:
    """Serialize the graph declaration JSON Schema deterministically."""
    return json.dumps(graph_schema_value(), indent=2, sort_keys=True) + "\n"
