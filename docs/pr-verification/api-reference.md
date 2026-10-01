# Python API reference PR verification

Base: `25738cac1cf9ffc9e60b4680bfc0185398939446`. The candidate SDK and
workspace were installed from this branch with Python 3.13.15 into
`/private/tmp/m3-docs-splits/api-reference-venv`; the imported package path is
`/private/tmp/m3-docs-splits/api-reference-rendering/sdk/src/m3/__init__.py`.

The five documentation checks passed: examples and navigation were current,
66 pages validated, 6 manifests validated, and the generated API inventory was
current. The renderer regression test passed (`1 passed in 1.56s`), including
representative properties, default factories, enum values, and exclusion of
Pydantic internals. Ruff check/format and YAML parsing passed.

The inventory uses Python 3.13.15 to keep inspected annotations stable. This
change describes public SDK surface and adds no runtime behavior or exports.
