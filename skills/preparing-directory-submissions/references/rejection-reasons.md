# Rejection reasons and the checks that cover them

Use this page to map pasted rejection feedback to checks. Match on meaning,
not exact words: reviewers paraphrase. Each feedback point gets a row at the
top of the report that names the check(s) it maps to, and those checks run
first, for every tool.

When a point matches nothing here, add a row with status `not checked`, quote
the point, and say which part of the server you'd need to see to check it.

Sources: "published" means the directory's own pages list it (excerpt id in
brackets); "reported" means a builder published the feedback they received.

## OpenAI (ChatGPT and Codex)

| Feedback, as reviewers word it | Source | Checks |
|---|---|---|
| "Tool hint annotations do not appear to match the tool's behavior" | published (`openai-reject-hints`) | B for every tool (all three hints), then lint `directory-hints-not-explicit` and `directory-hints-contradict` |
| "We're unable to connect to your MCP server using the MCP URL and/or test credentials we were given" | published (`openai-reject-connect`) | I (connectivity, origin, 401), H (account without MFA, codes or magic links) |
| "One or more of your test cases did not produce correct results" | published (`openai-reject-test-cases`) | test cases and their M3 direct tests; F for extraneous fields and personal identifiers |
| "Your plugin returns user-related data types that are not disclosed in your privacy policy" | published (`openai-reject-privacy`) | F |
| Publishing under an unverified individual or business name | published (`openai-organization-verification`) | not checked by anyone (business verification) |
| Description doesn't explain behavior, limitations or side effects | published (`openai-descriptions`) | B (side effects), A |
| Prefers itself over other plugins, or broad triggering | published (`openai-fair-play`, `openai-descriptions`) | A |
| Generic executor, operation selection | published (`openai-tool-independence`) | C |
| Asks for conversation history, location, credentials | published (`openai-minimal-inputs`, `openai-restricted-data`) | D |
| Sells subscriptions, credits or digital goods; promotes upgrades | published (`openai-commerce`) | E, G |
| Money transfers, crypto transfers, trades | published (`openai-prohibited-financial`) | E |
| Name ends in "MCP", "Plugin"; generic name; pricing in the subtitle; listing text too long; icon not square | published (`openai-names-and-descriptions`, `openai-listing-limits`, `openai-icons`) | G |
| Hidden or implicit side effects | published (`openai-predictable-behavior`) | B, J.2 |
| Domain verification failed | published (`openai-domain-verification`) | I |

## Claude

| Feedback, as reviewers word it | Source | Checks |
|---|---|---|
| Description tells Claude to prefer this tool, avoid other tools or web search, call it first or last | published (`claude-prompt-injection`); reported (https://github.com/mapbox/mcp-server/pull/273) | A |
| Instructs Claude to call tools the user didn't request | published (`claude-prompt-injection`, `claude-policy-2D`) | A |
| Hidden, obfuscated or encoded instructions | published (`claude-prompt-injection`, `claude-policy-2G`) | lint `directory-hidden-content`, then A |
| A tool accepts both safe and unsafe methods; catch-all `api_request` | published (`claude-separate-read-write`) | lint `directory-read-write-tool`, C |
| Free-form query tool with no API reference | published (`claude-api-docs-in-query-tools`) | C |
| Missing title, readOnlyHint or destructiveHint | published (`claude-tool-annotations`, `claude-policy-5E`) | lint `directory-tool-title-missing`, `directory-claude-safety-hints` |
| Annotations or description don't match behavior | published (`claude-accurate-descriptions`, `claude-policy-2B`) | B |
| Tool name over 64 characters | published (`claude-tool-name-length`) | lint `directory-tool-name-too-long` |
| Generic errors such as "Internal Server Error"; a tool fails with valid input | published (`claude-functional-quality`, `claude-policy-5A`) | test cases and their M3 direct tests |
| Collects conversation data, queries chat history or memory | published (`claude-functional-quality`, `claude-policy-1G`) | D |
| Transfers money or crypto; generates images, video or audio | published (`claude-unsupported-use-cases`, `claude-policy-4A`, `claude-policy-4B`) | E |
| Server calls APIs you don't own; domain doesn't match the service | published (`claude-api-ownership`, `claude-policy-3F`) | not checked by M3; ask the user and record their answer |
| Missing or incomplete privacy policy | published (`claude-local-privacy-policy`, `claude-policy-3A`) | F |
| No test account, or the account has no data | published (`claude-submission-materials`, `claude-policy-3D`) | H |
| Fewer than three working example prompts | published (`claude-policy-3E`) | test cases |
| Server is local, or not reachable over https; sign-in doesn't start | published (`claude-remote-https`, `claude-auth-401`) | I |

## Muse

| Feedback, as reviewers word it | Source | Checks |
|---|---|---|
| Tool classified as a write or sensitive write but documented as a read | published (`muse-3.2-classification`) | J.1, J.2, B |
| Hidden send, share, publish or payment | published (`muse-3.2-classification`) | J.2 |
| Descriptions not factual; steer away from competitors | published (`muse-3.2-factual-descriptions`) | A, J.3 |
| Hands the user off to the website; task not completed in Muse | published (`muse-1.1-complete-the-task`, `muse-2-example`) | J.4 |
| No value beyond the browser; wrapper around public pages | published (`muse-1.2-beyond-the-browser`) | J.4 |
| Money movement or trades | published (`muse-4.6-finance`) | E |
| Too much data requested; credentials outside secure flows | published (`muse-4.2-minimum-data`, `muse-4.4-secrets`) | D, F, J.5 |
| Documentation missing classes, side effects, errors or rate limits | published (`muse-5.4-documentation`) | J.7 |
| No dedicated test account | published (`muse-5.5-test-account`) | H, J.8 |
| Business verification | published (`muse-5.2-business-verification`) | not checked by anyone |
