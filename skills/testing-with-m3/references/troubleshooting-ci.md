<!-- Generated from docs/site/troubleshooting/ci.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# CI credentials and uploads

## A secret is missing despite `.env` or `--env-file`

An ambient variable takes precedence even when its value is empty. Unset it if
the file should supply the value. M3 does not interpolate dotenv values.

## Upload failed after tests completed

The failure line names the cause and says what to do next:

- `retry with m3 upload RUN_ID`: the M3 server was unreachable or temporarily
  unavailable. Keep the local database and feedback directory and run that
  command with the same project/database.
- `fix the cause and rerun tests`: retrying the same run cannot succeed. For
  example, the server rejected the report, a report exceeded its size limit, or
  the run output contains a credential from the test environment. Fix the
  cause, then rerun the tests to create a new run.

`m3 ci test` checks the run for these local problems right after pytest
finishes. Without `--upload`, it prints `upload inspection failed: …` and keeps
the test result.

## Token no longer works

Create a replacement on your organization's **CI tokens** page in the
[M3 account console](https://auth.sineframe.com/account) and update the
`M3_ACCESS_TOKEN` secret in the CI provider. Verify an upload with the
replacement, then revoke the old CI token on the same page. `m3 auth logout`
acts only on the saved CLI credential; it does not rotate or revoke a CI token.
