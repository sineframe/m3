---
title: "Run M3 in GitHub Actions"
description: "Start with a credential-free direct-server job. This complete workflow assumes the repository declares its project dependencies and contains the tests from the first-test guide."
---

# Run M3 in GitHub Actions

Start with a credential-free direct-server job. This complete workflow assumes
the repository declares its project dependencies and contains the tests from
the first-test guide.

```yaml
name: M3 tests
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  m3-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.11"
      - run: uv sync --locked
      - run: uv tool install sf-m3-cli
      - run: m3 setup
      - run: m3 ci test -- tests/
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: m3-results
          path: .m3/
          if-no-files-found: ignore
```

Pin the CLI to the version used by the project when adopting this workflow.
Agent, judge, and publishing jobs need separate credentials. Fork pull requests
do not receive repository secrets; do not run fork code in a privileged job
that has those secrets.
