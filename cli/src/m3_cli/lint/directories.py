"""Directory policy sources for `m3 lint --directory`.

Every source quotes a published policy verbatim, as rendered text. A quote
may elide text with `` … ``; its pieces then appear in that order inside the
section the URL anchors (and, with ``item``, inside that lettered item).
``benchmarks/lint-oracle/oracle.py --policies`` checks every quote against the
live pages; update the quotes and ``POLICY_DATE`` together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Directory = Literal["claude", "openai", "muse"]
DIRECTORIES: tuple[Directory, ...] = ("claude", "openai", "muse")
DIRECTORY_NAMES: dict[Directory, str] = {
    "claude": "Claude connector directory",
    "openai": "OpenAI plugin directory (ChatGPT and Codex)",
    "muse": "Muse connector directory",
}

# The day every quote below was last checked against its page.
POLICY_DATE = "2026-10-18"

DirectorySeverity = Literal["error", "warning"]
Detection = Literal["exact", "heuristic"]


@dataclass(frozen=True)
class Source:
    directory: Directory
    url: str
    quote: str
    # The lettered item inside the anchored section, on pages that anchor
    # whole sections only (the Claude policy: "2G").
    item: str | None = None


@dataclass(frozen=True)
class DirectoryEffect:
    directory: Directory
    severity: DirectorySeverity
    source: str
    summary: str
    # "exact" when every matching tool breaks the quoted rule; "heuristic"
    # when the condition only suggests it (the summary then says "may").
    detection: Detection
    # Why the condition breaks the rule, and for heuristics which
    # legitimate tools it may also flag.
    rationale: str
    when: frozenset[str] | None = None


def parse_directories(values: list[str]) -> list[Directory]:
    from . import LintUsageError

    chosen: list[Directory] = []
    for value in values:
        name = value.strip().lower()
        names: tuple[Directory, ...]
        if name == "all":
            names = DIRECTORIES
        elif name in DIRECTORIES:
            names = tuple(d for d in DIRECTORIES if d == name)
        else:
            raise LintUsageError("--directory supports claude, openai, muse or all")
        chosen.extend(d for d in names if d not in chosen)
    return [d for d in DIRECTORIES if d in chosen]


_CLAUDE_CRITERIA = "https://claude.com/docs/connectors/building/review-criteria"
_CLAUDE_MCP = "https://claude.com/docs/connectors/building/mcp"
_CLAUDE_POLICY = (
    "https://support.claude.com/en/articles/"
    "13145358-anthropic-software-directory-policy"
)
_CLAUDE_POLICY_1 = f"{_CLAUDE_POLICY}#h_bc5419edab"
_CLAUDE_POLICY_2 = f"{_CLAUDE_POLICY}#h_7dac153147"
_CLAUDE_POLICY_4 = f"{_CLAUDE_POLICY}#h_f6c96a50f3"
_OPENAI = "https://developers.openai.com/plugins/plugin-guidelines"
_MUSE = "https://platform.muse.ai/docs"
_PROMPT_INJECTION = f"{_CLAUDE_CRITERIA}#avoid-prompt-injection-patterns"
_REJECTED_IF = "Tool descriptions are rejected if they: … "
_RESTRICTED = (
    "Restricted data: Do not collect, solicit, or process the following "
    "categories of Restricted Data: … "
)

SOURCES: dict[str, Source] = {
    # Claude
    "claude-tool-annotations": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#provide-tool-annotations",
        "Every tool must include a title and the applicable hint: readOnlyHint: "
        "true for read-only tools, and destructiveHint: true for tools that "
        "modify or delete data.",
    ),
    "claude-declare-both-hints": Source(
        "claude",
        f"{_CLAUDE_MCP}#tool-hints",
        "All MCP tools must declare both of these annotations: … readOnlyHint: "
        "the tool only reads data … destructiveHint: the tool can modify or "
        "delete data",
    ),
    "claude-separate-read-write": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#separate-read-and-write-tools",
        "A single tool that accepts both safe HTTP methods, such as GET, HEAD, "
        "and OPTIONS, and unsafe methods, such as POST, PUT, PATCH, and DELETE, "
        "is rejected. Don't ship a catch-all api_request tool with a method "
        "parameter.",
    ),
    "claude-split-catch-all": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#separate-read-and-write-tools",
        "Split a catch-all tool into a read-only tool and one or more write "
        "tools. … The operations must be in separate tools.",
    ),
    "claude-query-api-docs": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#reference-api-docs-in-custom-query-tools",
        "If a tool accepts freeform endpoint paths, query strings, or request "
        "bodies that the caller constructs, its description must include a "
        "link to or the explicit name of the target API.",
    ),
    "claude-tool-name-length": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#keep-tool-names-short",
        "Tool names must be 64 characters or fewer.",
    ),
    "claude-no-interference": Source(
        "claude",
        _PROMPT_INJECTION,
        _REJECTED_IF + "Interfere with Claude calling other tools",
    ),
    "claude-no-unrequested-calls": Source(
        "claude",
        _PROMPT_INJECTION,
        _REJECTED_IF
        + "Instruct Claude to call external software or tools the user didn't request",
    ),
    "claude-no-override": Source(
        "claude",
        _PROMPT_INJECTION,
        _REJECTED_IF + "attempt to override system instructions",
    ),
    "claude-no-promotion": Source(
        "claude", _PROMPT_INJECTION, _REJECTED_IF + "promote products and services"
    ),
    "claude-policy-guardrails": Source(
        "claude",
        _CLAUDE_POLICY_1,
        "Software must not evade or enable users to circumvent Claude's safety "
        "guardrails, system instructions, or sandbox environments.",
        item="B",
    ),
    "claude-policy-context": Source(
        "claude",
        _CLAUDE_POLICY_1,
        "Software must only collect data from the user\u2019s context that is "
        "necessary to perform their function. Software must not collect "
        "extraneous conversation data, even for logging purposes.",
        item="D",
    ),
    "claude-policy-define-tools": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must define each tool or capability through "
        "narrow, unambiguous natural language that specifies what it does and "
        "when it should be invoked.",
        item="A",
    ),
    "claude-policy-coerce": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must not intentionally call or coerce Claude "
        "into calling other external software, tools, databases, or resources "
        "unless requested and intended by a user.",
        item="D",
    ),
    "claude-policy-interfere": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must not attempt to interfere with Claude "
        "calling tools from other software, tools, databases, or resources "
        "unless requested and intended by a user.",
        item="E",
    ),
    "claude-policy-hidden": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must not contain hidden, obfuscated, or encoded "
        "instructions. All behavioral guidance must be human-readable and "
        "clearly presented.",
        item="G",
    ),
    "claude-policy-money": Source(
        "claude",
        _CLAUDE_POLICY_4,
        "we do not allow Software with certain capabilities into our "
        "Directories. … Software that transfers money, cryptocurrency, or other "
        "financial assets, or executes financial transactions on behalf of users.",
    ),
    "claude-policy-ai-media": Source(
        "claude",
        _CLAUDE_POLICY_4,
        "we do not allow Software with certain capabilities into our "
        "Directories. … Software that uses AI models to generate images, video, "
        "or audio content. Design-focused software that uses AI models to "
        "create visual aids (such as slides, diagrams, charts, UI mockups, "
        "logos, or other design assets) are permitted.",
    ),
    # OpenAI
    "openai-explicit-hints": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "Set readOnlyHint, destructiveHint, and openWorldHint to explicit "
        "boolean values (true or false) in each tool\u2019s annotations object.",
    ),
    "openai-read-only-hint": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "readOnlyHint: Use true for retrieval, computation, or previews that do "
        "not modify the environment. Use false for external-state changes, "
        "persisting artifacts, starting stateful jobs or workflows, queuing "
        "work, or write-style outbound actions such as posting messages, "
        "sending emails, or uploading files.",
    ),
    "openai-hints-exclusive": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "readOnlyHint: Use true for retrieval, computation, or previews that do "
        "not modify the environment. Use false for external-state changes … "
        "destructiveHint: Use false for read-only tools.",
    ),
    "openai-read-only-not-destructive": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "destructiveHint: Use false for read-only tools.",
    ),
    "openai-destructive-hint": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "For writes, use true for potentially destructive or irreversible "
        "effects, such as deletion, overwriting, cancellation, access "
        "revocation, or irreversible sends or transactions. Use false only for "
        "additive writes without destructive or irreversible effects. Being "
        "able to undo an action does not, by itself, justify setting "
        "destructiveHint to false.",
    ),
    "openai-separate-operations": Source(
        "openai",
        f"{_OPENAI}#tool-independence-and-exposure",
        "Expose each model-callable operation as a separate tool with a clear "
        "description, input schema, and annotations. Do not use discovery, "
        "operation selection, or schema fetching with a generic executor to "
        "enable operations not individually exposed for review.",
    ),
    "openai-fair-play": Source(
        "openai",
        f"{_OPENAI}#fair-play",
        "Plugins must not include descriptions, titles, tool annotations, or "
        "other model-readable fields, at either the tool or plugin level, that "
        "manipulate how the model selects or uses other plugins or their tools "
        "(for example, instructing the model to prefer one plugin over others) "
        "or interfere with fair discovery.",
    ),
    "openai-broad-triggering": Source(
        "openai",
        f"{_OPENAI}#descriptions-that-match-behavior",
        "They must not recommend overly broad triggering beyond the explicit "
        "user intent and purpose the plugin fulfills.",
    ),
    "openai-user-intent": Source(
        "openai",
        f"{_OPENAI}#respect-user-intent",
        "Act only within the user-authorized task. Plugin and tool metadata "
        "must not seek secrets or protected context, direct unauthorized data "
        "transfers or actions, override platform instructions or safeguards, "
        "conceal behavior, or impersonate another party. Encoded or obfuscated "
        "instructions do not exempt a plugin from these requirements.",
    ),
    "openai-description-required": Source(
        "openai",
        f"{_OPENAI}#descriptions-that-match-behavior",
        "Each tool must have a nonempty description that explicitly and "
        "accurately explains its purpose and behavior, when to use it, and any "
        "relevant limitations or side effects.",
    ),
    "openai-unique-names": Source(
        "openai",
        f"{_OPENAI}#clear-and-accurate-tool-names",
        "Tool names must be unique within your MCP server.",
    ),
    "openai-conversation-history": Source(
        "openai",
        f"{_OPENAI}#minimal-and-purpose-driven-inputs",
        "Do not request the full conversation history, raw chat transcripts, or "
        "broad contextual fields \u201cjust in case.\u201d",
    ),
    "openai-prior-turns": Source(
        "openai",
        f"{_OPENAI}#minimal-and-purpose-driven-inputs",
        "Do not request accumulated conversation context or prior-turn arrays.",
    ),
    "openai-precise-location": Source(
        "openai",
        f"{_OPENAI}#minimal-and-purpose-driven-inputs",
        "Do not request precise user location data (for example, GPS "
        "coordinates or addresses).",
    ),
    "openai-restricted-credentials": Source(
        "openai",
        f"{_OPENAI}#data-collection",
        _RESTRICTED + "Access credentials and authentication secrets (such as "
        "API keys, MFA/OTP codes, or passwords).",
    ),
    "openai-restricted-payment-card": Source(
        "openai",
        f"{_OPENAI}#data-collection",
        _RESTRICTED + "Information subject to Payment Card Information Data "
        "Security Standards (PCI DSS)",
    ),
    "openai-restricted-government-id": Source(
        "openai",
        f"{_OPENAI}#data-collection",
        _RESTRICTED + "Government identifiers (such as social security numbers)",
    ),
    "openai-financial-services": Source(
        "openai",
        f"{_OPENAI}#commerce-and-monetization",
        "plugins may not be used to sell, promote, facilitate, or meaningfully "
        "enable the following goods or services: … Prohibited fraudulent, "
        "deceptive, or high-risk services … Execution of money "
        "transfers, crypto transfers, or investment trades",
    ),
    # Muse
    "muse-tool-descriptions": Source(
        "muse",
        f"{_MUSE}#32-how-tools-are-classified",
        "Tool descriptions must be factual. Do not use tool descriptions or "
        "responses to instruct Muse to ignore its rules, steer away from "
        "competitors, access unrelated data, force recommendations, conceal "
        "commercial interests, or take unrequested actions.",
    ),
    "muse-secrets": Source(
        "muse",
        f"{_MUSE}#44-security",
        "Protect authentication credentials, payment data, and other secrets. "
        "Do not expose them in prompts, tool descriptions or responses, URLs, "
        "analytics, or logs; collect and transmit them only through secure "
        "flows.",
    ),
    "muse-finance": Source(
        "muse",
        f"{_MUSE}#46-finance-connectors",
        "We allow Connectors that have read access to financial data, but are "
        "not currently approving new financial money movement tools, such as "
        "transfers between financial accounts, or tools that place financial "
        "trade orders at this time.",
    ),
}
