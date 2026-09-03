Quality Graph turns repository-owned checks into a native GitHub Actions pipeline.
You describe commands, environments, and dependencies in `qg.yaml`; Quality Graph
generates the workflows and connects their results to pull-request reporting and governance.

```yaml
version: 0
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: alchemmist/quality-graph@<exact-commit-sha>

profiles:
  default:
    runner: ubuntu-latest
    setup:
      - uses: actions/checkout@v7

nodes:
  lint:
    run: make lint

  test:
    needs: [lint]
    run: make test
    results:
      junit: reports/tests.xml
```

Each node remains an ordinary command or reusable action. `needs` defines execution order, and
`results` lets Quality Graph render structured output such as test failures.

Generate the GitHub workflows from the declaration:

```bash
qg generate
```

Generated workflows keep native jobs, logs, statuses, and retries. The declaration remains the
source of truth.

[Get started →](quickstart.md)

<section class="adopters" aria-labelledby="adopters-title">
  <p id="adopters-title">Already running on</p>
  <div class="adopters-window">
    <div class="adopters-track">
      <div class="adopters-set">
        <a class="adopter" href="https://github.com/alchemmist/monori">
          <img src="assets/monori.svg" alt="">
          <span>monori</span>
        </a>
        <a class="adopter" href="https://github.com/alchemmist/quality-graph">
          <img src="assets/quality-graph-eye.svg" alt="">
          <span>quality—graph</span>
        </a>
        <a class="adopter" href="https://github.com/alchemmist/lazy-tmux">
          <img src="assets/lazy-tmux.svg" alt="">
          <span>lazy-tmux</span>
        </a>
      </div>
      <div class="adopters-set" aria-hidden="true">
        <span class="adopter">
          <img src="assets/monori.svg" alt="">
          <span>monori</span>
        </span>
        <span class="adopter">
          <img src="assets/quality-graph-eye.svg" alt="">
          <span>quality—graph</span>
        </span>
        <span class="adopter">
          <img src="assets/lazy-tmux.svg" alt="">
          <span>lazy-tmux</span>
        </span>
      </div>
    </div>
  </div>
</section>
