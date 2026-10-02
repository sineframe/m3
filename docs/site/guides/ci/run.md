---
title: "Run M3 tests in CI"
description: "m3 ci test uses the normal project Python, storage, harness, and pytest selection. It excludes tests whose nearest M3 marker sets ci=False."
---

# Run M3 tests in CI

`m3 ci test` uses the normal project Python, storage, harness, and pytest
selection. It excludes tests whose nearest M3 marker sets `ci=False`.

```python
import pytest

pytestmark = pytest.mark.m3(suite_name="shipping")


@pytest.mark.m3(ci=False)
def test_requires_a_person_at_the_keyboard():
    answer = input("Type yes after checking the test system: ")
    assert answer == "yes"
```

Run the credential-free selection locally before adding a workflow:

```sh
m3 setup
m3 ci test -- tests/
```

Ordinary `m3 test` still includes the marked test. Paths, `-k`, `-m`, and
`--suite` combine with the CI exclusion. Publishing is separate and occurs
only when `--upload` is present.

Continue to [GitHub Actions](github-actions.md).

See [managed runtime selection](../agents/managed-runtimes.md) when a CI test
must use a pinned native harness version.
