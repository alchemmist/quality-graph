"""Resolve portable event membership and dependency policy for hosting providers."""

from dataclasses import replace

from quality_graph_core.graph import DependencyPolicy, Graph


def project_event(graph: Graph, event: str, *, flow_id: str | None = None) -> Graph:
    """Select one event without silently discarding its required dependencies."""
    if graph.flows:
        matches = tuple(
            flow
            for flow in graph.flows
            if flow.trigger == event and (flow_id is None or flow.id == flow_id)
        )
        if len(matches) != 1:
            message = f"expected one flow for event {event} and placement {flow_id}"
            raise ValueError(message)
        return graph.for_flow(matches[0].id)
    if flow_id is not None:
        message = "legacy event projections do not have explicit flow identifiers"
        raise ValueError(message)
    nodes = tuple(node for node in graph.nodes if not node.events or event in node.events)
    if graph.execution.get(event, DependencyPolicy.GRAPH) is DependencyPolicy.NONE:
        nodes = tuple(replace(node, needs=()) for node in nodes)
    else:
        membership = {node.id for node in nodes}
        for node in nodes:
            missing = set(node.needs) - membership
            if missing:
                message = (
                    f"{event} event projection excludes dependencies of {node.id}: "
                    f"{', '.join(sorted(missing))}"
                )
                raise ValueError(message)
    return replace(graph, nodes=nodes)
