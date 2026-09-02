# Quality Graph

Quality Graph describes portable quality checks and their execution relationships independently
of a specific CI provider.

## Language

**Execution event**:
A provider-recognized occasion on which a quality graph can run.
_Avoid_: Trigger, workflow type

**Event projection**:
The subset of graph nodes selected for one execution event together with their applicable
dependency relationships.
_Avoid_: Event graph, filtered graph

**Dependency policy**:
The rule that determines whether an event projection preserves or removes declared scheduling
dependencies.
_Avoid_: Parallel mode, DAG mode
