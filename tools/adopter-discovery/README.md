# Quality Graph adopter discovery

Find public GitHub repositories with strong evidence of active Quality Graph usage and rank them
by stars.

```bash
GITHUB_TOKEN=… uv run --project tools/adopter-discovery --locked qg-find-adopters
```

The tool reads `docs/adopters.json` from the current repository by default and marks its entries as
already listed. Use `--new-only` to show only candidates that are absent from the ticker.

From the Quality Graph repository, `make adopters-find` runs this new-candidates-only mode, while
`make users` shows all candidates. Both targets load `GITHUB_TOKEN` from the repository-root `.env`.
