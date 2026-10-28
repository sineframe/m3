# OpenAI plugin directory (ChatGPT and Codex): policy excerpts

Last checked: **2026-10-27**. Every excerpt below is copied verbatim from the
linked page; `…` marks text left out. Quote the excerpt and its link behind every failure you report.
Before quoting, re-read the live page when you can (see
[Re-read the live policy](../SKILL.md#re-read-the-live-policy)) and say in the
report if the text changed.

Pages:

- Plugin guidelines: https://developers.openai.com/plugins/plugin-guidelines
- App review: https://developers.openai.com/plugins/deploy/app-review
- Submission: https://developers.openai.com/plugins/deploy/submission

Note: the phrase "manipulative ranking language" does not appear on these
pages as of 2026-10-27. Quote [openai-fair-play](#openai-fair-play),
[openai-descriptions](#openai-descriptions) or
[openai-tool-names](#openai-tool-names) for steering and ranking language.

## Listing

### openai-names-and-descriptions

https://developers.openai.com/plugins/plugin-guidelines#plugin-name-description-and-prompts

> Names: Avoid overly generic names, especially single-word dictionary terms
> that aren’t explicitly tied to your brand. Do not append “MCP,” “MCP Server,”
> or “Plugin” to a company or product name.
>
> Subtitles and descriptions: Describe the plugin’s functionality without
> comparing it with other products, disparaging alternatives, or making
> unverifiable claims. Do not advertise pricing, subscriptions, free trials,
> discounts, or promotions.

### openai-listing-limits

https://developers.openai.com/plugins/deploy/submission#listing-metadata

> displayName: Name shown to users; required, at most 30 characters.
>
> shortDescription: Subtitle shown with the name; required, at most 30
> characters.
>
> longDescription: Description of tasks, intended users, and limitations;
> required, at most 4000 characters.
>
> developerName: Publisher name; required, at most 80 characters.
>
> category: Required category title from the dashboard, such as Productivity
> or Developer Tools.
>
> capabilities: Capability labels; at most 20, with at most 120 characters
> each. Required in the Codex format; use [] if there are none.
>
> websiteURL: HTTPS product website, at most 1024 characters.
>
> supportURL: HTTPS customer support page, at most 1024 characters.
>
> privacyPolicyURL: HTTPS privacy policy, at most 1024 characters.
>
> termsOfServiceURL: HTTPS terms of service, at most 1024 characters.

> For an attached MCP app that needs public review, all four listing URLs are
> required: websiteURL, supportURL, privacyPolicyURL, and termsOfServiceURL.
> Use HTTPS URLs without embedded credentials.

(The page lays these out as a table of field, requirement, type and limit; the
lines above join each field name to its "value and submission limit" cell.)

### openai-icons

https://developers.openai.com/plugins/deploy/submission#icons-and-screenshots

> Include logo and composerIcon in packages you prepare for distribution.
> Codex package validation requires both; you can upload a portable package
> without them, but the dashboard requires a primary app icon before
> submission.

> Supported image formats are PNG, JPEG, WebP, and SVG, at most 5 MiB each.
> Icons and logos must be square and at least 48 by 48 pixels. Raster images
> can be at most 4096 pixels in either dimension. For SVG icons, provide square
> numeric dimensions or a square viewBox of at least 48 by 48.

## Safety and fair play

### openai-respect-user-intent

https://developers.openai.com/plugins/plugin-guidelines#respect-user-intent

> Provide experiences that directly address the user’s request. Do not insert
> unrelated content, attempt to redirect the interaction, or collect data
> beyond what is reasonably necessary to fulfill the user’s request and what is
> consistent with your privacy policy.
>
> Act only within the user-authorized task. Plugin and tool metadata must not
> seek secrets or protected context, direct unauthorized data transfers or
> actions, override platform instructions or safeguards, conceal behavior, or
> impersonate another party. Encoded or obfuscated instructions do not exempt a
> plugin from these requirements.

### openai-fair-play

https://developers.openai.com/plugins/plugin-guidelines#fair-play

> Plugins must not include descriptions, titles, tool annotations, or other
> model-readable fields, at either the tool or plugin level, that manipulate
> how the model selects or uses other plugins or their tools (for example,
> instructing the model to prefer one plugin over others) or interfere with
> fair discovery. All descriptions must accurately reflect the plugin’s value
> without disparaging alternatives.

### openai-third-party

https://developers.openai.com/plugins/plugin-guidelines#third-party-content-and-integrations

> Unofficial connectors: We cannot approve plugins that primarily function as
> unofficial connectors to third-party services, including pass-through
> intermediary software layers.

## Privacy

### openai-privacy-policy

https://developers.openai.com/plugins/plugin-guidelines#privacy-policy

> Plugin submissions must include a clear, published privacy policy
> explaining, at minimum, the categories of personal data collected, the
> purposes of use, the categories of recipients, data retention timelines, and
> any controls offered to your users. Follow this policy at all times.

### openai-restricted-data

https://developers.openai.com/plugins/plugin-guidelines#data-collection

> Collection minimization: Gather only the minimum data required to perform the
> plugin’s function. Inputs should be specific, narrowly scoped, and explicitly
> linked to the task. Avoid “just in case” fields or broad profile data.
>
> Restricted data: Do not collect, solicit, or process the following categories
> of Restricted Data:
>
> - Information subject to Payment Card Information Data Security Standards
>   (PCI DSS)
> - Protected health information (PHI)
> - Government identifiers (such as social security numbers)
> - Access credentials and authentication secrets (such as API keys, MFA/OTP
>   codes, or passwords).

## Commerce

### openai-commerce

https://developers.openai.com/plugins/plugin-guidelines#commerce-and-monetization

> Currently, plugins may conduct commerce only for physical goods. Selling
> digital products or services—including subscriptions, digital content,
> tokens, or credits—is not allowed, whether offered directly or indirectly
> (for example, through freemium upsells).
>
> Users may sign in to an existing paid account and access features already
> included in their subscription. Plugins must not display subscription plans,
> initiate new subscriptions, or promote upgrades.

### openai-prohibited-financial

https://developers.openai.com/plugins/plugin-guidelines#prohibited-fraudulent-deceptive-or-high-risk-services

> Execution of money transfers, crypto transfers, or investment trades

(One entry in the list of prohibited fraudulent, deceptive, or high-risk
services.)

### openai-advertising

https://developers.openai.com/plugins/plugin-guidelines#advertising

> Plugins must not serve advertisements and must not exist primarily as an
> advertising vehicle.

## Tools

### openai-tool-names

https://developers.openai.com/plugins/plugin-guidelines#clear-and-accurate-tool-names

> Tool names should be human-readable, specific, and descriptive of what the
> tool actually does.
>
> Tool names must be unique within your MCP server.
>
> Use plain language that directly reflects the action, ideally as a verb (for
> example, get_order_status).
>
> Avoid misleading, overly promotional, or comparative language (for example,
> pick_me, best, official).

### openai-descriptions

https://developers.openai.com/plugins/plugin-guidelines#descriptions-that-match-behavior

> Each tool must have a nonempty description that explicitly and accurately
> explains its purpose and behavior, when to use it, and any relevant
> limitations or side effects. Descriptions must match the tool’s schema and
> actual behavior. If a tool’s behavior is unclear or incomplete from its
> description, the plugin may be rejected.
>
> Descriptions must not favor or disparage other plugins or services or attempt
> to influence the model to select them over another plugin’s tools. They must
> not recommend overly broad triggering beyond the explicit user intent and
> purpose the plugin fulfills.

### openai-tool-independence

https://developers.openai.com/plugins/plugin-guidelines#tool-independence-and-exposure

> Each public tool must operate independently. Do not instruct the model to
> invoke another plugin or connector to complete its operation; implement
> required dependencies within your plugin.
>
> Expose each model-callable operation as a separate tool with a clear
> description, input schema, and annotations. Do not use discovery, operation
> selection, or schema fetching with a generic executor to enable operations
> not individually exposed for review.

### openai-correct-annotation

https://developers.openai.com/plugins/plugin-guidelines#correct-annotation

> Set readOnlyHint, destructiveHint, and openWorldHint to explicit boolean
> values (true or false) in each tool’s annotations object. …
>
> readOnlyHint: Use true for retrieval, computation, or previews that do not
> modify the environment. Use false for external-state changes, persisting
> artifacts, starting stateful jobs or workflows, queuing work, or write-style
> outbound actions such as posting messages, sending emails, or uploading
> files.
>
> destructiveHint: Use false for read-only tools. For writes, use true for
> potentially destructive or irreversible effects, such as deletion,
> overwriting, cancellation, access revocation, or irreversible sends or
> transactions. Use false only for additive writes without destructive or
> irreversible effects. Being able to undo an action does not, by itself,
> justify setting destructiveHint to false.
>
> openWorldHint: Use true for public or open-ended entities, including
> read-only web search and arbitrary destinations. A tool confined to a bounded
> private account, workspace, or catalog may use false, even when externally
> hosted. Connectivity alone does not determine this value.

### openai-minimal-inputs

https://developers.openai.com/plugins/plugin-guidelines#minimal-and-purpose-driven-inputs

> Do not request the full conversation history, raw chat transcripts, or broad
> contextual fields “just in case.”

> Do not request accumulated conversation context or prior-turn arrays.
>
> If needed, rely on the coarse geographic location shared by the system. Do
> not request precise user location data (for example, GPS coordinates or
> addresses).

### openai-predictable-behavior

https://developers.openai.com/plugins/plugin-guidelines#predictable-auditable-behavior

> Tools should behave exactly as their names, descriptions, and inputs
> indicate.
>
> Side effects should never be hidden or implicit.

### openai-response-minimization

https://developers.openai.com/plugins/plugin-guidelines#tool-data-handling

> Response minimization: Tool responses must return only data that is directly
> relevant to the user’s request and the tool’s stated purpose. Do not include
> diagnostic, telemetry, or internal identifiers—such as session IDs, trace
> IDs, request IDs, timestamps, or logging metadata—unless they are strictly
> required to fulfill the user’s query.

> Your MCP server must not pull, reconstruct, or infer the full chat log from
> the client or elsewhere.

## Review access, test cases and connectivity

### openai-test-credentials

https://developers.openai.com/plugins/plugin-guidelines#test-credentials

> When submitting a plugin with an authenticated MCP server, provide a login
> and password for a fully featured demo account that includes sample data.
> Plugins that require additional login steps, such as a new account sign-up
> or 2FA through an inaccessible account, will be rejected.

### openai-review-information

https://developers.openai.com/plugins/deploy/submission#complete-review-information

> Reviewer credentials, if sign-in is required. Provide a dedicated test
> account, login URL, workspace or tenant details, and sign-in instructions.
> Use sample data, not a real user’s account. The account needs the
> permissions and data required by your test cases and should work immediately
> without MFA approval, email or SMS codes, magic links, or private-network
> access.
>
> Five positive test cases. Include the scenario, user prompt, expected tools,
> and expected result. Run each case using the test account before submitting.
>
> Three negative test cases. Include a prompt or scenario where the plugin
> should not act, why it should not complete the request, and the expected
> refusal, clarification, or safe fallback.

### openai-test-case-count

https://developers.openai.com/plugins/deploy/submission#configure-onboarding-review-and-publication

> Initial MCP app review requires exactly five positive and three negative
> cases. Upload accepts partial lists, so finish them before submitting.

### openai-remote-server

https://developers.openai.com/plugins/deploy/app-review#remote-mcp-server-requirements

> Your MCP server is hosted on a publicly accessible domain
>
> You are not using a local or testing endpoint

### openai-domain-verification

https://developers.openai.com/plugins/deploy/submission#domain-verification-details

> For domain verification, host the exact challenge token as plain text at the
> URL shown in the portal:
> https://<challenge-base-host>/.well-known/openai-apps-challenge
>
> Return only the exact token—not JSON or a list of tokens. The challenge base
> must be an HTTPS origin on the MCP hostname or an eligible parent domain;
> paths are ignored.

### openai-organization-verification

https://developers.openai.com/plugins/deploy/app-review#organization-verification

> This is enforced during review. Publishing under an unverified individual or
> business name will result in rejection.

## Published rejection reasons

https://developers.openai.com/plugins/deploy/app-review#review-and-approval-faqs

The FAQ "What are common rejection reasons and how can I resolve them?" lists
these, verbatim:

### openai-reject-connect

https://developers.openai.com/plugins/deploy/app-review#review-and-approval-faqs

> We’re unable to connect to your MCP server using the MCP URL and/or test
> credentials we were given.
>
> For servers requiring authentication, our review team must be able to log
> into a demo account with no further configuration required.
>
> Ensure that the provided URL and credentials are correct, do not feature MFA
> (including requiring SMS codes, login through systems that require SMS,
> email or other verification schemes).

### openai-reject-test-cases

https://developers.openai.com/plugins/deploy/app-review#review-and-approval-faqs

> One or more of your test cases did not produce correct results.
>
> …
>
> Ensure that the returned textual output closely adheres to the user’s
> request, and does not offer extraneous information that is irrelevant to the
> request, including personal identifiers.

### openai-reject-privacy

https://developers.openai.com/plugins/deploy/app-review#review-and-approval-faqs

> Your plugin returns user-related data types that are not disclosed in your
> privacy policy.
>
> Audit your MCP tool responses in ChatGPT by running a few realistic example
> requests and listing every user-related field the server returns (including
> nested fields and “debug” payloads). Ensure tools return only what’s strictly
> necessary for the user’s request and remove any unnecessary PII,
> telemetry/internal identifiers (for example, session, trace, or request IDs;
> timestamps; internal account IDs; or logs) and any auth secrets (tokens,
> keys, or passwords).

### openai-reject-hints

https://developers.openai.com/plugins/deploy/app-review#review-and-approval-faqs

> Tool hint annotations do not appear to match the tool’s behavior:
>
> readOnlyHint: Set to true if it strictly fetches/looks up/lists/retrieves
> data and does not modify anything. Set to false if the tool can
> create/update/delete anything, trigger actions (send emails/messages, run
> jobs, enqueue tasks, write logs, start workflows), or otherwise change state.
>
> Destructive hint: Set the destructive annotation to true if the tool can
> cause irreversible outcomes (deleting, overwriting, sending messages or
> transactions you can’t undo, revoking access, or destructive admin actions),
> even in only select modes, through default parameters, or through indirect
> side effects. …
>
> openWorldHint: Set to true if the tool accesses the public internet or
> open-ended external entities. This includes read-only tools such as web
> search and write tools that post to public platforms, send messages to
> external recipients, publish content, push code, or submit forms. Set to
> false if the tool is limited to a bounded private account or workspace, even
> when that service is externally hosted.
