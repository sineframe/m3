# Credentials documentation PR verification

Base: `25738cac1cf9ffc9e60b4680bfc0185398939446`. Candidate SDK, app, and CLI
were installed from this branch with Python 3.13.15 into
`/private/tmp/m3-docs-splits/credentials-venv`; all three packages report
`0.2.0a13`.

The five documentation checks passed: examples and navigation were current,
68 pages validated, 7 manifests validated, and the canonical API inventory was
current. The copied credentials project passed all 3 tests; the reference
value excerpt passed 1 test; the existing CLI credential suites passed 20
tests in 38.25 seconds. Example credentials are fixed dummy values, and the
HTTP and ACP fixtures are local. No live provider or account credential was
used.
