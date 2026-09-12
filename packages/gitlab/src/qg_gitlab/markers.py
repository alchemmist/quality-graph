"""Encode acknowledgements of installed trusted pipeline statuses."""


def gate_marker(project: int, pipeline: int, flow: str, digest: str) -> str:
    """Bind initial pipeline admission to one declaration and flow."""
    return f"<!-- qg:gitlab:gate:{project}:{pipeline}:{flow}:{digest} -->"


def execution_marker(project: int, pipeline: int, job: int, digest: str) -> str:
    """Require a new publisher acknowledgement for every retried job."""
    return f"<!-- qg:gitlab:job:{project}:{pipeline}:{job}:{digest} -->"
