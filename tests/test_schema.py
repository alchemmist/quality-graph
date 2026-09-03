import json

from quality_graph_core.schema import (
    graph_schema_json,
    graph_schema_value,
    result_schema_json,
    result_schema_value,
)


def test_result_schema_is_deterministic_and_describes_protocol_contract() -> None:
    serialized = result_schema_json()
    schema = json.loads(serialized)

    assert serialized == result_schema_json()
    assert schema == result_schema_value()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schemaVersion"] == {"const": 0}
    assert schema["additionalProperties"] is False
    assert "failureKind" not in schema["required"]
    assert schema["allOf"][0]["then"] == {"required": ["failureKind"]}


def test_graph_schema_is_deterministic_and_describes_declaration_contract() -> None:
    serialized = graph_schema_json()
    schema = json.loads(serialized)

    assert serialized == graph_schema_json()
    assert schema == graph_schema_value()
    assert schema["properties"]["version"] == {"const": 0}
    assert schema["properties"]["profiles"]["required"] == ["default"]
    assert schema["$defs"]["node"]["oneOf"] == schema["$defs"]["step"]["oneOf"]
    provider = schema["properties"]["provider"]["oneOf"][1]
    configuration = provider["properties"]["configuration"]
    assert configuration["properties"]["default-branch"]["minLength"] == 1
    assert configuration["properties"]["merge"]["properties"]["required"] == {"type": "boolean"}
    assert configuration["additionalProperties"] is True
