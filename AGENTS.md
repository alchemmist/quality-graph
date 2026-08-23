# Project instructions

Read [TASK.md](TASK.md) completely before planning or changing product behaviour. It is
the source of truth for scope, public interfaces, architectural constraints, quality
standards, and completion criteria. Create an implementation plan from the current
repository state; `TASK.md` is a product specification, not a task checklist.

Use `~/code/monori-2` as the source implementation and engineering baseline. Inspect the
relevant Quality Graph code, CI libraries, tests, workflows, Make targets, and tool
configuration before extracting each behaviour. Preserve proven security and lifecycle
semantics while removing Monori-specific assumptions.

Design deep modules. The public seam is the declarative graph plus the versioned result
protocol. Keep GitHub transport, artifacts, comments, authorization, rendering limits,
and workflow mechanics behind that seam. Tests and callers use the same public
interfaces.

Maintain the quality bar specified in `TASK.md`. In particular, Ruff uses
`select = ["ALL"]`, mypy is strict, changed production code has complete coverage, core
decision logic is mutation-tested, and all relevant local and CI operations run through
Make targets.

Write code without comments unless a non-obvious constraint cannot be expressed through
naming, types, or module structure. Document public interfaces and user-visible
behaviour. Fix root causes and keep changes surgical.

During initial bootstrapping, commit directly to `main`. Keep commits atomic, coherent,
and valid. Commit messages are one-line, lowercase, imperative English without
co-authors. Keep generated outputs in the same commit as their source changes. All
public documentation and GitHub communication is in English.

After the bootstrap phase, use GitHub issues, issue-named branches, and pull requests as
directed by the project owner. Use GitHub integrations rather than the `gh` CLI for
GitHub operations.
