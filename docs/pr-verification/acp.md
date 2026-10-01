# ACP documentation PR verification

Base: `25738cac1cf9ffc9e60b4680bfc0185398939446`. Candidate: branch-local
editable `sf-m3` `0.2.0a13`, installed with Python 3.13.15 into
`/private/tmp/m3-docs-splits/acp-venv`. Import identity resolved to
`/private/tmp/m3-docs-splits/acp/sdk/src/m3/__init__.py`.

The five documentation checks passed: example rendering and navigation were
current, 69 pages validated, and 8 example manifests validated. The focused
ACP proof passed (`2 passed in 5.04s`) from clean copies outside the checkout.
It ran the deterministic local-agent connection example, the environment
manifest variation using the same local ACP process, and the custom-wrapper
example. Ruff check and formatting passed. The workflow YAML parsed with
Ruby's YAML parser.

The ACP fixtures verify local protocol and MCP evidence. They do not verify a
third-party ACP implementation or live provider behavior.
