# Administrator commands

Commands are complete pull-request comments:

```text
/qg status
/qg help
/qg ignore <finding-or-node>[,<target>...]
/qg remove-ignore <finding-or-node>[,<target>...]
/qg ignore-file <path>[,<path>...]
/qg remove-ignore-file <path>[,<path>...]
```

State-changing commands require a configured collaborator role. Targets must exist in the
newest valid result artifacts and be approvable under the base graph policy.

An accepted command creates a transparent immutable authorization record and reruns failed
jobs. Reactions show acknowledgement, success, invalid input, or forbidden authorization.

Approvals suppress matching quality findings without deleting them. They remain visible,
support exact inverse commands, survive reruns and line movement, and disappear from effective
state when their finding, file, or node target no longer exists.

Editing a hidden marker, a pull-request body, or ordinary Markdown cannot create approval.
