# Quality Graph Core

Platform-independent graph, result protocol, policy, and provider contracts for Quality Graph.

```python
from quality_graph_core import Graph, Result

graph = Graph.from_yaml(source)
result = Result.from_json(payload)
```

Providers implement the runtime-checkable `Provider` interface and return a deterministic
`GeneratedProject`. Core never imports a provider or the CLI.
