---
title: "Credential reference"
description: "Credential types, resolution points, precedence, validation, and isolation boundaries."
---

# Credential reference

Credentials are consumer-specific. Endpoint authentication, native-agent login, ACP child environment, judge API access, M3 run upload, and GitHub metadata access are separate channels. A value is not forwarded from one channel to another unless an explicit mapping or reference says so. See [Configure credentials](../guides/credentials.md) for a local endpoint test and CI example.

## `SecretReference`

Public frozen value object:

```python
from m3.types import SecretReference

endpoint_key = SecretReference(source="environment", name="MCP_ENDPOINT_KEY")
```

| Field | Type | Default | Contract |
| --- | --- | --- | --- |
| `source` | `Literal["environment", "provider"]` | required | Selects the resolver namespace. Environment is the built-in direct-transport source. Native harness and ACP child adapters currently accept environment references only. |
| `name` | `str` | required | Length 1 through 256. It names the environment variable or provider entry; it is not the credential value. |

The model forbids unknown fields, is immutable, and serializes only the source and name. It does not load `.env`. A resolver obtains values only when the connection or child launch needs them.

## MCP endpoint credentials

`StdioServer.environment` and `HTTPServer.headers` accept string values or `SecretReference` values. HTTP also supports `bearer_token: SecretReference | None = None` and `auth: httpx2.Auth | None = None` on the direct client API.

For `source="environment"`, the default direct-client resolver reads the named value from the process environment. For `source="provider"`, supply a custom object with a `resolve(reference) -> str` method to the direct client's `secret_resolver` option; it can read from the provider-specific namespace your application uses. M3 does not call a cloud vault or provider SDK. Missing and empty values raise `TransportConnectionError(transport="streamable_http", phase="authentication")` on direct HTTP resolution and `TransportStartupError` for stdio resolution. Native process and ACP manifest lookups fail with `HarnessStartupError` before the child begins its work. Error messages omit the missing value.

HTTP header values are resolved when the connection is created. A name containing CR/LF is rejected. The default resolver observes secret references and headers classified as sensitive so M3 can redact their values at observation boundaries. `bearer_token` adds `Authorization: Bearer <value>`. If the resolved `headers` already contain an Authorization name in any case, the bearer option raises a typed authentication error. Select one Authorization source. The `auth` callback is handed to the `httpx2` client; M3 does not serialize it or infer its challenge behavior. Tests against a specific callback/server pair are required before claiming that exchange works.

`HTTPServer.url` is an endpoint, not a credential field. Query parameters with credential-shaped names are rejected by endpoint validation. For local HTTP, explicitly set `trust=TrustLevel.TRUSTED_PRIVATE` when binding a private/loopback endpoint.

## Native harness credentials

`Codex`, `Pi`, `ClaudeCode`, and `OpenCode` have `credential_references: Mapping[str, SecretReference] = {}`. CLI and pytest `credential_env` is instead `Mapping[target_name, source_environment_name]`; it is converted to environment references. CLI mapping syntax is `[KIND:]TARGET=SOURCE`, with accepted kinds Codex, Pi, Claude Code (including Claude aliases), OpenCode, ACP for validation, and `judge`. The SDK's native selector uses the same target-to-source shape.

Credential inference and native login differ by adapter:

| Adapter | Inferred key source/target | Process authentication and isolation |
| --- | --- | --- |
| Codex | A non-empty `OPENAI_API_KEY` in the invoking environment maps to child target `OPENAI_API_KEY`; explicit target-to-source mappings can add or override references. | M3 creates a temporary `CODEX_HOME`. If there are no credential references, it copies host `CODEX_HOME/auth.json` or `~/.codex/auth.json` into that temporary home only when the source is a regular non-symlink file; the copy is created with mode `0600`. When any refs exist, host auth is not copied. |
| Claude Code | A non-empty `ANTHROPIC_API_KEY` is inferred for that target; explicit mappings can name other target/source pairs. | Launch uses a temporary HOME and XDG config/data/state/cache directories with a limited environment. Host Claude login files are not copied into the temporary HOME. |
| Pi | `opencode/...`, `openai/...`, and `anthropic/...` map `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`; `openai-codex/...` maps `PI_CODING_AGENT_DIR` (a provider data directory, not an API key). Explicit maps are supported. | Provider and model prefix must agree when both specify a provider. References resolve from environment; this is a selected child mapping, not a general copy of host CLI login state. |
| OpenCode | Recognized prefixes `opencode/...`, `openai/...`, and `anthropic/...` infer `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`; explicit maps are supported. | Launch uses temporary HOME and XDG directories, fixed locale/time values, and selected provider variables. Host provider login files are not copied by the adapter. |

Explicit mappings use `target=source`: the target variable is the one the child receives, and the source name is read from the invoking environment. Inference applies only when the matching source value is non-empty. Current native resolution expects `source="environment"`. Missing/empty values produce typed startup failures. A mapped API key does not prove that the provider accepts it.

`--credential-env` is not ACP manifest injection. Use the manifest `env` object described below for ACP child environment references. Harness-target mappings do not automatically supply the judge key.

## ACP child environment

The validator accepts precisely these manifest keys; unknown fields are rejected:

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `schema_version` | literal string | `"m3.harness.v1"` | Defaults to this literal; any other value is invalid. |
| `protocol` | literal string | `"acp"` | Defaults to this literal; any other value is invalid. |
| `protocol_version` | integer literal `1` | `1` | Defaults to 1. |
| `command` | string | required | Length 1 through 1000. |
| `args` | string array | `[]` | Each argument is passed as a distinct argv element. |
| `env` | string-to-string object | `{}` | Child variable names must be identifiers; every value must match `${ENV_NAME}` exactly. Literal secrets, prefixed/suffixed placeholders, and non-reference values are invalid. |

At launch, M3 resolves each referenced parent environment value. The ACP child gets a temporary `HOME`, XDG directories, an executable search path derived from the agent command's directory and the system default, fixed locale/time/encoding values, and only the manifest environment entries in addition to that baseline. The isolated HOME is removed during cleanup. ACP does not inherit the caller’s arbitrary environment, and it does not use native managed-runtime downloads. Readiness can report whether command and refs are available, but does not start the agent or contact its provider. An unresolved or empty value causes a typed harness startup failure.

## Judge credentials

`LLMJudge(api_key_env="M3_JUDGE_API_KEY")` uses that environment variable for bearer authentication; `M3_JUDGE_API_KEY` is the default name. Custom endpoints require an explicit response mode. `auth="none"` disables API-key authentication. If the key is absent or empty, the judge returns the typed `judge_credentials_missing` evaluation status rather than borrowing a native agent credential. In CLI/pytest mapping, use `judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY` to map a distinct source variable.

## M3 upload PAT and local keyring

`m3 auth login`, `m3 auth status`, and `m3 auth logout` manage the PAT used for M3 control-plane access:

| Input or command | Behavior |
| --- | --- |
| `M3_ACCESS_TOKEN` | If present, it takes precedence over saved keyring state. It must match the M3 PAT format and decode to the expected identifier and secret lengths. An explicitly empty value is an error. |
| Local CLI with no environment token | `m3 ci test --upload` looks for a PAT in the supported OS credential manager. If none is saved, it directs the user to login or provide the environment variable. |
| CI with no environment token | `m3 ci test --upload` fails with `M3_ACCESS_TOKEN is required in CI`; it does not read an interactive user keyring. |
| `m3 auth login` | Uses a loopback PKCE callback and saves the token in a supported OS keyring under a service/account keyed to the configured HTTPS control-plane origin. Metadata contains token ID and expiry, not the token. If supported secure storage is unavailable, login refuses to store the PAT. The sign-in page says older tokens remain active until revoked there. |
| `m3 auth status` | Reports local environment/keyring presence. It does not call the server to test token validity. |
| `m3 auth logout` | Removes the saved token for the configured origin and its local metadata. It does not revoke copies previously exported elsewhere. The sign-in page is where older tokens can be revoked. |

The accepted token form is `m3pat_<identifier>.<secret>`, where the identifier is a canonical unpadded base64url encoding of 16 bytes and the secret is a canonical unpadded base64url encoding of 32 bytes. `M3_CONTROL_PLANE_URL`, when set, must be an HTTPS origin only: no credentials, path, query, or fragment. If unset, the CLI uses its configured default origin.

`M3_ACCESS_TOKEN` cannot be either the source or target of a `--credential-env` mapping. On the `m3 ci test` path, the upload PAT is removed from the pytest child environment. `m3 ci test` only uploads when `--upload` is supplied.

For `--env-file PATH`, the CLI reads that path explicitly and disables dotenv interpolation. There is no implicit `.env` discovery. The ambient process environment is copied first; file values fill only absent names, so an ambient value wins even if it is empty. A blank dotenv `M3_ACCESS_TOKEN` placeholder is treated as absent, while ambient `M3_ACCESS_TOKEN=""` is rejected. The SDK itself does not parse dotenv files. Do not infer that using `--env-file` affects a direct SDK process that did not receive or read the file.

## GitHub metadata token

`M3_GITHUB_TOKEN` is a distinct optional token used only when the managed-runtime code downloads metadata from `https://api.github.com`. It is not the M3 PAT, agent credential, or judge API key. Metadata requests do not require this token for all URLs. Redirect handling removes authorization when the destination changes; do not set this variable to grant general GitHub API access to an agent process.

## Evidence, storage, and redaction boundaries

Durable specifications and profiles should store `SecretReference`, never resolved values. Durable serialization rejects literal values in recognized credential-bearing fields rather than relying on later redaction. At transport/harness observation boundaries, resolved values are registered for redaction; sensitive key names and URL query names are also classified. These controls do not make arbitrary application logs, prompts, environment dumps, third-party process output, or every external provider artifact safe. Never print credentials or place them in a prompt or source file.

M3 CI upload code checks payload bodies for the upload PAT and known secrets before sending, and the uploader uses a private local cache with restrictive file modes. This describes the exercised uploader path, not a universal no-persistence guarantee for arbitrary test output, platform caches, shell history, CI logs, or provider-side records. Review the data emitted by your tests before enabling upload.

Evidence basis: candidate source at untagged commit `25738ca` plus this docs change; `sdk/src/m3/_types/base.py::SecretReference`, `sdk/src/m3/transport/direct.py::{EnvironmentSecretResolver,resolve_headers}`, `sdk/src/m3/transport/local.py::StdioMCPTransport._environment`, adapter credential code in `sdk/src/m3/harness/{codex,claude,pi,opencode}.py`, `sdk/src/m3/harness/native.py::{_isolated_environment,_resolve_runtime_value}`, `sdk/src/m3/harness/manifest.py::validate_manifest`, `sdk/src/m3/judges.py::LLMJudge`, `cli/src/m3_cli/{ci_credentials,auth,control_plane}.py`, and tests `sdk/tests/e2e/test_secret_references.py`, `sdk/tests/integration/test_secret_canaries.py`, `sdk/tests/unit/test_direct_transports.py`, `cli/tests/test_ci_credentials.py`, and `cli/tests/test_cli_credentials_integration.py`. Deterministic local endpoint and stdio fixture tests passed. Real vendor login, remote PAT validity/revocation, all platform keyring backends, and external auth-provider behavior remain unverified.
