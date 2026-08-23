from quality_graph.controls import (
    control_commands,
    control_marker,
    control_states,
    decode_control_marker,
    render_control,
)
from quality_graph.result import Control, ControlKind


def test_controls_encode_reversible_commands_for_every_scope() -> None:
    finding = Control(ControlKind.FINDING, "finding", checked=True)
    file = Control(ControlKind.FILE, "src/app.py")
    node = Control(ControlKind.NODE, "lint")

    assert control_commands(finding) == (
        "/qg ignore finding",
        "/qg remove-ignore finding",
    )
    assert control_commands(file) == (
        "/qg ignore-file src/app.py",
        "/qg remove-ignore-file src/app.py",
    )
    assert control_commands(node) == (
        "/qg ignore lint",
        "/qg remove-ignore lint",
    )
    assert decode_control_marker(control_marker(finding)) == control_commands(finding)


def test_control_renderer_and_state_parser_share_canonical_marker() -> None:
    control = Control(ControlKind.FINDING, "finding", checked=True)
    rendered = render_control(control)

    assert rendered.startswith("- [x] finding: `finding`")
    assert control_states(rendered) == {control_marker(control): True}


def test_control_decoder_rejects_malformed_payloads() -> None:
    assert decode_control_marker("quality-graph-control:invalid") is None
    assert decode_control_marker("quality-graph-control:L3FnIGhlbHA") is None
    assert control_states("ordinary Markdown") == {}
