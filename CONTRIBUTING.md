# Contributing

Issues are welcome: wrong verdicts (with the transactions that show it), missing service labels,
new data sources, and bugs. For anything larger than a small fix, open an issue first so we can
agree on the approach.

Before sending a pull request:

```sh
uv sync --all-extras --group dev
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
cd web && pnpm install && pnpm typecheck && pnpm lint && pnpm test
```

Rules that keep the data trustworthy:

- Every number shown anywhere must be computed from data the code fetched, never typed in.
- A new rule for joining the trail needs a test with a case it accepts and a case it rejects, and a
  line in [docs/method.md](docs/method.md).
- Never add a personal name to a label. Labels describe services, not people.

Outside contributions are accepted under a contributor license agreement, which will be linked
here before the first one is merged.
