# Muse connector directory (Meta): policy excerpts

Last checked: **2026-10-27**. Every excerpt below is copied verbatim from the
linked page; `…` marks text left out. Quote the excerpt and its link behind every failure you report.
Before quoting, re-read the live page when you can (see
[Re-read the live policy](../SKILL.md#re-read-the-live-policy)) and say in the
report if the text changed.

Page: https://platform.muse.ai/docs (Connector guidelines)

Muse reviews your documentation and classifies each tool itself. Tool
annotations in the Muse portal are "coming soon" (5.6), so `m3 lint` reports
Muse as not checked. Every Muse row in a readiness report is judged.

What the page does not say, as of 2026-10-27: it does not mention AI image,
video or audio generation, it does not ask for example prompts by name (5.1
asks for "supported tasks"), and it does not name conversation history,
government IDs or precise location. Cite 4.2 and 4.4 for data requests.

## Completion and value

### muse-1.1-complete-the-task

https://platform.muse.ai/docs#11-complete-the-task

> In the eyes of the user, Muse should be able to finish all tasks your
> connector starts, within Muse’s approved tool scope.

> Sign-in, payment verification, and required identity checks may need user
> input. Preserve progress so Muse can resume afterward. Sending the user to
> finish the entire checkout does not meet the completion requirement.

### muse-1.2-beyond-the-browser

https://platform.muse.ai/docs#12-add-value-beyond-the-browser

> Muse can already browse websites. Your connector should improve on what’s
> possible in the browser experience through better information, account
> access, more reliable actions, or fewer steps. Show the difference with a
> specific use case.
>
> A read-only connector can qualify when the information is the outcome, such
> as an account report or live inventory analysis. A wrapper around public
> pages or a list of links alone is not enough.

### muse-2-example

https://platform.muse.ai/docs#2-example-cedar-stays

> Don’t: Return generic links to browser booking flows.

> Don’t: Send users back to the website for supported follow-up tasks.

(Two "Don't" cells from the Cedar Stays table.)

## Tool permissions

### muse-3.1-read-only-access

https://platform.muse.ai/docs#31-offer-read-only-account-access

> When using OAuth to connect to a user’s account, offer users the option to
> limit Muse’s permissions to Read Only, where possible.

### muse-3.2-classification

https://platform.muse.ai/docs#32-how-tools-are-classified

> In the review process we’ll review your documentation and classify each tool
> into:
>
> Read: Retrieves information without changing data, account state, or
> permissions. Examples include searching availability and checking a booking.
>
> Write: Creates, changes, or deletes data, changes permissions, or acts on the
> user’s behalf. Examples include saving a favorite or creating a reservation.
>
> Sensitive write: Certain actions that produce irreversible or consequential
> actions (make a purchase, send an email, etc.)
>
> A tool that combines reads and writes must be classified as a write. Never
> hide a send, share, publish, or payment action in a tool described as a read
> or routine edit.

### muse-3.2-factual-descriptions

https://platform.muse.ai/docs#32-how-tools-are-classified

> Tool descriptions must be factual. Do not use tool descriptions or responses
> to instruct Muse to ignore its rules, steer away from competitors, access
> unrelated data, force recommendations, conceal commercial interests, or take
> unrequested actions.

### muse-3.4-scope-of-approval

https://platform.muse.ai/docs#34-respect-the-scope-of-approval

> Respect Muse’s approval and safety controls. Do not bypass them, ask users to
> disable them, or reuse approval for unrelated actions. Do not add products,
> tips, subscriptions, or fees without authorization.

## Trust and integrity

### muse-4.2-minimum-data

https://platform.muse.ai/docs#42-ask-for-only-required-data-and-use-it-only-to-provide-your-connector

> Use the minimum data required for each tool to provide its advertised
> functionality safely and reliably.

### muse-4.3-accurate

https://platform.muse.ai/docs#43-be-accurate-and-transparent

> Accurately describe your identity, affiliations, capabilities, pricing,
> availability, and results. Disclose account, plan, region, age, and usage
> restrictions before use. Label advertising, sponsored results, and commercial
> relationships that influence recommendations. Do not fabricate reviews,
> urgency, discounts, or successful outcomes.

### muse-4.4-secrets

https://platform.muse.ai/docs#44-security

> Protect authentication credentials, payment data, and other secrets. Do not
> expose them in prompts, tool descriptions or responses, URLs, analytics, or
> logs; collect and transmit them only through secure flows.

### muse-4.6-finance

https://platform.muse.ai/docs#46-finance-connectors

> We allow Connectors that have read access to financial data, but are not
> currently approving new financial money movement tools, such as transfers
> between financial accounts, or tools that place financial trade orders at
> this time.

### muse-4.7-transactions

https://platform.muse.ai/docs#47-transactions

> Before commitment, your tools should return all relevant information, such
> as: the seller, full price and currency, recurring charges, key terms, and
> cancellation or refund policy. Follow the approval requirements in 3.4.
>
> Distinguish quoted, pending, confirmed, failed, and cancelled actions. Report
> uncertain outcomes honestly and check their status before retrying. Prevent
> duplicate transactions.

## Submission

### muse-5.1-overview

https://platform.muse.ai/docs#51-connector-overview

> Describe the intended users, supported tasks, how the connector improves on
> browser use, and any account, plan, or regional restrictions.

### muse-5.2-business-verification

https://platform.muse.ai/docs#52-business-verification-and-terms

> Provide the business information and supporting evidence requested to verify
> your organization and your authority to submit the connector.

### muse-5.3-data-questionnaire

https://platform.muse.ai/docs#53-data-processing-questionnaire

> Explain what data the connector accesses and why, how it is protected, who it
> is shared with, how long it is kept, and how it is deleted. Ensure your
> answers reflect how the connector works and match your privacy policy.

### muse-5.4-documentation

https://platform.muse.ai/docs#54-integration-credentials-and-tool-documentation

> In your API or MCP documentation, include setup instructions, inputs and
> outputs, read/write classifications, sensitive designations, permissions,
> side effects, status handling, errors, and rate limits.

### muse-5.5-test-account

https://platform.muse.ai/docs#55-test-user-credentials-and-demo

> Provide a dedicated test account so reviewers can connect your service and
> exercise each submitted tool. Include sign-in instructions, required
> permissions, representative test data, and any account or regional limits.
> Keep the account accessible throughout review. For transaction tools, explain
> how to test checkout without real charges.

### muse-5.6-annotations

https://platform.muse.ai/docs#56-tool-annotation-coming-soon

> You’ll be able to classify each tool as Read or Write and mark Sensitive
> writes in the portal. For now, include these annotations in your tool
> documentation, following Section 3.

### muse-6-no-guarantee

https://platform.muse.ai/docs#6-review-process

> We review submissions in three stages. Meeting these guidelines does not
> guarantee approval.
