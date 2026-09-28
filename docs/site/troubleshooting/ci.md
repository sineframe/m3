# CI credentials and uploads

## A secret is missing despite `--env-file`

An ambient variable takes precedence even when its value is empty. Unset it if
the file should supply the value. M3 does not interpolate dotenv values.

## Upload failed after tests completed

Keep the local database and feedback directory. Capture the run ID printed by
that invocation and use `m3 upload RUN_ID` with the same project/database.

## Token no longer works

Create a replacement token, update the CI secret, verify a run, and revoke the
old token. `m3 auth logout` removes only the local copy.
