---
title: "Credential reference"
description: "Resolution and validation behavior for endpoint, agent, judge, ACP, and upload credentials."
---

# Credential reference

## Credential references

`SecretReference(source, name)` stores a source and a variable or provider entry name, not the resolved value. The type is immutable, accepts `source="environment"` or `source="provider"`, and does not read dotenv files. Endpoint definitions accept references in stdio environment values, HTTP headers, and the direct HTTP bearer-token option.

```python
from m3.types import SecretReference

endpoint_key = SecretReference(source="environment", name="MCP_ENDPOINT_KEY")
```

## Direct MCP endpoints

For direct HTTP, the default environment resolver treats a missing or empty environment value as an authentication failure: `TransportConnectionError(transport="streamable_http", phase="authentication")`. A custom resolver controls its own lookup behavior. Header names and values containing CR or LF also fail during authentication setup.

For stdio, an environment `SecretReference` fails with `TransportStartupError` when its source name is absent. A present empty value is copied to the child as an empty string. A custom resolver determines its own behavior. These are direct transport results; they do not describe native harness or ACP launch behavior.

## Native harnesses

`Codex`, `Pi`, `ClaudeCode`, and `OpenCode` accept `credential_references: Mapping[str, SecretReference]`. SDK selection and CLI/pytest `--credential-env` map a child target name to a source environment name. Supported agent and judge mappings reserve the exact name `M3_ACCESS_TOKEN` as either target or source. This check applies to those mapping entry points; it is not a general boundary against aliases, copied values, or arbitrary application code.

Native inference uses non-empty standard variables for the selected harness and model. SDK selectors check that an explicitly mapped source name exists when building the execution specification; an empty value can therefore pass selection and fail later at launch. Native launch raises `HarnessStartupError` when it cannot resolve a non-empty referenced value. An empty value in an explicitly supplied lookup falls back to the ambient process environment if that name has a non-empty value. A mapping does not verify that a provider accepts the value.

Codex, Claude Code, Pi, and OpenCode isolate their child environments. Codex may copy a host authentication file only when there are no explicit credential references. The other native adapters use selected references and do not copy host login files into their temporary homes.

## ACP environment

An ACP manifest `env` value must be an exact `${ENV_NAME}` reference. M3 resolves it from the parent environment before starting the ACP process. A missing reference causes `HarnessStartupError`; a present empty value is forwarded as empty. The child receives the configured environment entries and an isolated HOME, not the caller's full environment. ACP manifest validation rejects `M3_ACCESS_TOKEN` as a child target or as the referenced source name.

## LLM judge

`LLMJudge` uses `M3_JUDGE_API_KEY` by default for the default endpoint. A custom endpoint requires an explicit `response_mode`; with `auth="env"`, it also requires an explicit `api_key_env`. The exact `M3_ACCESS_TOKEN` name is not accepted for `api_key_env`.

`auth="none"` is allowed only for `localhost`, `127.0.0.1`, or `::1`. A remote endpoint with no authentication is rejected. A missing or empty judge key returns an evaluation with status `ERROR` and code `judge_credentials_missing`; it does not fail harness startup or borrow an agent key.

## M3 upload

`M3_ACCESS_TOKEN` is the CLI credential for uploading an M3 run. It has the form `m3pat_<22 base64url characters>.<43 base64url characters>`; an explicitly empty or malformed token is a CLI configuration error. On `m3 ci test`, M3 removes the token from the pytest child environment. An environment token takes precedence over the local credential store; CI requires the environment token and does not read an interactive keyring. See [`m3 auth`](cli/index.md#m3-auth) or [Manage access tokens](../guides/ci/access.md) for login and keyring commands. The explicit `--env-file` behavior is described in [Configuration](configuration.md).
