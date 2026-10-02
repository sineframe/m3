# Contributing to M3

Start with [Architecture](architecture.md) to understand the package boundary.
Use the package READMEs for local development commands and
[Releasing](releasing.md) for release work.

Product documentation lives in `docs/site`. Before changing it, read the
[Documentation writing guide](documentation-writing-guide.md). Runnable guide
examples live under `sdk/examples/docs` and are part of the documentation
contract.

Run the repository checks relevant to the files you changed. At minimum,
validate the docs manifest and links:

```sh
python3 scripts/render_docs_examples.py --check
python3 scripts/render_docs_navigation.py --check
python3 scripts/validate_docs_site.py
python3 scripts/validate_docs_examples.py
python3 scripts/render_skill_references.py --check
.venv/bin/python scripts/render_docs_api_reference.py --check
```

Render the combined site from the landing repository before merging a docs
change:

```sh
M3_DOCS_DIR=/path/to/m3 npm run build
```
