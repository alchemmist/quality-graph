"""Encode reversible dashboard controls without granting authority to Markdown."""

from __future__ import annotations

import base64
import html
import re

from quality_graph_core.result import Control, ControlKind

CONTROL_RE = re.compile(
    r"^- \[(?P<state>[ xX])] .*?<!-- (?P<marker>quality-graph-control:[A-Za-z0-9_-]+) -->$",
    re.MULTILINE,
)
CONTROL_COMMAND_COUNT = 2


def control_commands(control: Control) -> tuple[str, str]:
    """Return canonical apply and reverse commands for one control."""
    if control.kind is ControlKind.FILE:
        apply_name = "ignore-file"
        reverse_name = "remove-ignore-file"
    else:
        apply_name = "ignore"
        reverse_name = "remove-ignore"
    return (
        f"/qg {apply_name} {control.target}",
        f"/qg {reverse_name} {control.target}",
    )


def control_marker(control: Control) -> str:
    """Encode both canonical commands in one stable hidden marker."""
    payload = "\n".join(control_commands(control)).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"quality-graph-control:{encoded}"


def decode_control_marker(value: str) -> tuple[str, str] | None:
    """Decode a marker produced by the canonical renderer."""
    encoded = value.removeprefix("quality-graph-control:")
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    commands = payload.splitlines()
    if len(commands) != CONTROL_COMMAND_COUNT or any(
        not command.startswith("/qg ") for command in commands
    ):
        return None
    return commands[0], commands[1]


def control_states(body: str) -> dict[str, bool]:
    """Extract canonical checkbox states keyed by hidden marker."""
    return {
        match.group("marker"): match.group("state").lower() == "x"
        for match in CONTROL_RE.finditer(body)
    }


def render_control(control: Control, *, show_commands: bool = False) -> str:
    """Render one reversible checkbox control."""
    state = "x" if control.checked else " "
    marker = control_marker(control)
    target = _code(control.target)
    rendered = f"- [{state}] {control.kind.value}: `{target}` <!-- {marker} -->"
    if not show_commands:
        return rendered
    apply, reverse = control_commands(control)
    return f"{rendered}\n  - apply: `{_code(apply)}`\n  - reverse: `{_code(reverse)}`"


def _code(value: str) -> str:
    return html.escape(value).replace("`", "&#96;").replace("\n", "&#10;")
