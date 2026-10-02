<!-- Generated from docs/site/troubleshooting/ci.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# CI credentials and uploads

## A secret is missing despite `--env-file`

An ambient variable takes precedence even when its value is empty. Unset it if
the file should supply the value. M3 does not interpolate dotenv values.

## Upload failed after tests completed

Keep the local database and feedback directory. Capture the run ID printed by
that invocation and use `m3 upload RUN_ID` with the same project/database.

## Token no longer works

Create a replacement on your organization's **CI tokens** page in the
[M3 account console](https://auth.sineframe.com/account) and update the
`M3_ACCESS_TOKEN` secret in the CI provider. Verify an upload with the
replacement, then revoke the old CI token on the same page. `m3 auth logout`
acts only on the saved CLI credential; it does not rotate or revoke a CI token.
