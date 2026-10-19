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
_CLAUDE_POLICY_2 = f"{_CLAUDE_POLICY}#h_7dac153147"
_OPENAI = "https://developers.openai.com/plugins/plugin-guidelines"

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
    "claude-tool-name-length": Source(
        "claude",
        f"{_CLAUDE_CRITERIA}#keep-tool-names-short",
        "Tool names must be 64 characters or fewer.",
    ),
    "claude-policy-define-tools": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must define each tool or capability through "
        "narrow, unambiguous natural language that specifies what it does and "
        "when it should be invoked.",
        item="A",
    ),
    "claude-policy-hidden": Source(
        "claude",
        _CLAUDE_POLICY_2,
        "Instructional Software must not contain hidden, obfuscated, or encoded "
        "instructions. All behavioral guidance must be human-readable and "
        "clearly presented.",
        item="G",
    ),
    # OpenAI
    "openai-explicit-hints": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "Set readOnlyHint, destructiveHint, and openWorldHint to explicit "
        "boolean values (true or false) in each tool\u2019s annotations object.",
    ),
    "openai-hints-exclusive": Source(
        "openai",
        f"{_OPENAI}#correct-annotation",
        "readOnlyHint: Use true for retrieval, computation, or previews that do "
        "not modify the environment. Use false for external-state changes … "
        "destructiveHint: Use false for read-only tools.",
    ),
    "openai-separate-operations": Source(
        "openai",
        f"{_OPENAI}#tool-independence-and-exposure",
        "Expose each model-callable operation as a separate tool with a clear "
        "description, input schema, and annotations. Do not use discovery, "
        "operation selection, or schema fetching with a generic executor to "
        "enable operations not individually exposed for review.",
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
    # Muse: none. Muse classifies tools from their documentation, not MCP
    # annotations, and its listing rules are about what text means.
}
