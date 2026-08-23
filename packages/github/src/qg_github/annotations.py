"""Render safe bounded GitHub workflow annotations."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from quality_graph_core.result import Annotation, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

MAX_STEP_ANNOTATIONS = 10


@dataclass(frozen=True)
class AnnotationBatch:
    """Carry rendered annotations and the number omitted by GitHub limits."""

    annotations: tuple[Annotation, ...]
    omitted: int


def grouped_annotations(annotations: Iterable[Annotation]) -> AnnotationBatch:
    """Group duplicate locations and apply the GitHub step limit."""
    grouped: dict[tuple[object, ...], list[Annotation]] = {}
    source = tuple(annotations)
    for annotation in source:
        location = annotation.location
        key = (
            location.path,
            location.start_line,
            location.end_line,
            location.start_column,
            location.end_column,
            annotation.level,
            annotation.title,
        )
        grouped.setdefault(key, []).append(annotation)
    result = tuple(_merge_annotations(items) for items in grouped.values())
    selected = result[:MAX_STEP_ANNOTATIONS]
    return AnnotationBatch(selected, len(result) - len(selected))


def _merge_annotations(annotations: list[Annotation]) -> Annotation:
    first = annotations[0]
    messages = "\n".join(dict.fromkeys(annotation.message for annotation in annotations))
    return replace(first, message=messages)


def workflow_annotation_command(annotation: Annotation) -> str:
    """Render one escaped GitHub workflow command."""
    location = annotation.location
    properties = {
        "file": location.path,
        "line": str(location.start_line),
        "endLine": str(location.end_line),
    }
    if annotation.title is not None:
        properties["title"] = annotation.title
    if location.start_column is not None and location.end_column is not None:
        properties["col"] = str(location.start_column)
        properties["endColumn"] = str(location.end_column)
    encoded = ",".join(f"{key}={escape_property(value)}" for key, value in properties.items())
    command = "error" if annotation.level is Severity.ERROR else annotation.level.value
    return f"::{command} {encoded}::{escape_data(annotation.message)}"


def publish_annotations(
    annotations: Iterable[Annotation],
    *,
    stream: TextIO | None = None,
) -> None:
    """Publish grouped annotations and disclose omitted diagnostics."""
    output = stream or sys.stderr
    batch = grouped_annotations(annotations)
    output.writelines(
        f"{workflow_annotation_command(annotation)}\n" for annotation in batch.annotations
    )
    if batch.omitted:
        output.write("::notice::Additional diagnostics are available in the Job Summary.\n")


def escape_property(value: str) -> str:
    """Escape a workflow-command property."""
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def escape_data(value: str) -> str:
    """Escape workflow-command data."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
