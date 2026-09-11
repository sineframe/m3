# Current checklist: remove obsolete Streamlit UI

This is the authoritative checklist for removing the retired Streamlit
implementation. The historical SDK plans retain their original Streamlit
design narratives for context; those sections are superseded by this
remediation and are not future architecture.

- [ ] Remove the obsolete Streamlit package, tests, dependency extra, commands,
      and active documentation.
- [ ] Preserve the CLI-owned compiled SPA and its browser/E2E tests.

The supported browser experience is the compiled SPA served by `mcp_pal_cli.web`.
