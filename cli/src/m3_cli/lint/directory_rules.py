"""Directory readiness checks: server metadata against published directory rules.

Each effect cites a verbatim policy sentence from ``directories.SOURCES``. An
effect is an error only when every tool matching its condition breaks that
sentence, whatever the tool does; otherwise it is a warning whose summary
says "may". Checks that depend on what text means (steering, use cases, names
against hints) are not here: patterns can't decide them correctly.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterator
from typing import Any

from .collect import Snapshot
from .directories import DirectoryEffect
from .rules import Hit, Rule, _dict_subschemas, _schema, _short, _tools, _where

# --- closed lists -------------------------------------------------------------


HTTP_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
HTTP_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HTTP_METHODS = HTTP_SAFE | HTTP_UNSAFE | {"TRACE", "CONNECT"}
METHOD_NAMES = frozenset({"method", "httpmethod", "requestmethod"})


# --- helpers --------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _tokens(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [t.lower() for t in re.split(r"[\s_\-./:]+", spaced) if t]


def _squash(text: str) -> str:
    return " ".join(text.split())


def _annotations(tool: dict[str, Any]) -> dict[str, Any]:
    annotations = tool.get("annotations")
    return annotations if isinstance(annotations, dict) else {}


def _filled(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _title(tool: dict[str, Any]) -> str | None:
    for value in (tool.get("title"), _annotations(tool).get("title")):
        if _filled(value):
            return str(value)
    return None


def _hint_text(annotations: dict[str, Any], hint: str) -> str:
    if hint not in annotations:
        return f"{hint} absent"
    value = annotations[hint]
    if isinstance(value, bool):
        return f"{hint} {str(value).lower()}"
    return f"{hint} {_short(value, 30)} (not a boolean)"


# --- titles and hints -------------------------------------------------------------


def _check_title(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        if _title(tool) is None:
            yield Hit(_where(tool, "title"), "no title and no annotations.title")


def _check_claude_hints(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        annotations = _annotations(tool)
        read_only = annotations.get("readOnlyHint")
        destructive = annotations.get("destructiveHint")
        detail = ", ".join(
            _hint_text(annotations, h) for h in ("readOnlyHint", "destructiveHint")
        )
        if read_only is not True and not isinstance(destructive, bool):
            variant = "no-applicable"
        elif not isinstance(read_only, bool) and destructive is False:
            # Read-only or not is unknown, so the applicable hint may be missing.
            variant = "read-only-undeclared"
        elif not isinstance(read_only, bool) or not isinstance(destructive, bool):
            # readOnlyHint true, or destructiveHint true: the applicable hint is
            # there and only the MCP page's "declare both" is unmet.
            variant = "one-declared"
        elif read_only is False and destructive is False:
            variant = "write-not-destructive"
        else:
            continue
        yield Hit(_where(tool, "annotations"), detail, frozenset({variant}))


def _check_explicit_hints(snapshot: Snapshot) -> Iterator[Hit]:
    hints = ("readOnlyHint", "destructiveHint", "openWorldHint")
    for tool in _tools(snapshot):
        annotations = _annotations(tool)
        missing = [h for h in hints if not isinstance(annotations.get(h), bool)]
        if missing:
            detail = ", ".join(_hint_text(annotations, h) for h in missing)
            yield Hit(_where(tool, "annotations"), detail)


def _check_contradictory_hints(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        annotations = _annotations(tool)
        if (
            annotations.get("readOnlyHint") is True
            and annotations.get("destructiveHint") is True
        ):
            yield Hit(
                _where(tool, "annotations"), "readOnlyHint true, destructiveHint true"
            )


# --- read/write and free-form tools ---------------------------------------------

_UNREACHABLE = frozenset({"not", "if", "then", "else", "dependentSchemas"})


_MAX_DEPTH = 32


def _values(schema: dict[str, Any], depth: int = 0) -> set[str] | None:
    """The string values a schema allows, or None when it doesn't limit them
    (or nests combinators too deeply to tell)."""

    if depth > _MAX_DEPTH:
        return None
    limits: list[set[str]] = []
    if "const" in schema:
        limits.append({schema["const"]} if isinstance(schema["const"], str) else set())
    if isinstance(schema.get("enum"), list):
        limits.append({v for v in schema["enum"] if isinstance(v, str)})
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            union: set[str] = set()
            for branch in branches:
                values = (
                    _values(branch, depth + 1) if isinstance(branch, dict) else None
                )
                if values is None:
                    break
                union |= values
            else:
                limits.append(union)
    all_of = schema.get("allOf")
    for branch in all_of if isinstance(all_of, list) else []:
        values = _values(branch, depth + 1) if isinstance(branch, dict) else None
        if values is not None:
            limits.append(values)
    if not limits:
        return None
    return set.intersection(*limits)


def _opaque(schema: dict[str, Any]) -> bool:
    """True when conditionals or references could narrow the schema further."""

    return bool(_UNREACHABLE & schema.keys()) or "$ref" in schema


def _objects(tool: dict[str, Any]) -> Iterator[tuple[str, dict[str, dict[str, Any]]]]:
    """(pointer, constraints per property) for each reachable object schema.

    A property's constraints combine its own schema with the same property in
    the object's allOf branches. Objects under conditionals, inside allOf
    branches (merged into their parent) or carrying conditionals or references
    are skipped: their reachable values cannot be established.
    """

    for pointer, schema, position in _dict_subschemas(_schema(tool)):
        properties = schema.get("properties")
        if not isinstance(properties, dict) or position == "allOf":
            continue
        if _UNREACHABLE & set(pointer.split("/")) or _opaque(schema):
            continue
        all_of = schema.get("allOf")
        branches: list[Any] = all_of if isinstance(all_of, list) else []
        if any(not isinstance(b, dict) or _opaque(b) or "allOf" in b for b in branches):
            continue
        merged: dict[str, dict[str, Any]] = {}
        for name, prop in properties.items():
            if not isinstance(prop, dict) or _opaque(prop):
                continue
            extra = [
                b["properties"][name]
                for b in branches
                if isinstance(b.get("properties"), dict)
                and isinstance(b["properties"].get(name), dict)
            ]
            if any(_opaque(e) for e in extra):
                continue
            merged[name] = {"allOf": [prop, *extra]} if extra else prop
        yield pointer, merged


def _check_read_write(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        for pointer, props in _objects(tool):
            for name, prop in props.items():
                values = _values(prop)
                if _norm(name) not in METHOD_NAMES or values is None:
                    continue
                upper = {v.upper() for v in values}
                safe, unsafe = upper & HTTP_SAFE, upper & HTTP_UNSAFE
                # Only HTTP methods: an action enum may also contain "delete".
                if safe and unsafe and upper <= HTTP_METHODS:
                    yield Hit(
                        _where(tool, f"{pointer}/properties/{name}"),
                        f"accepts safe {', '.join(sorted(safe))} and unsafe "
                        f"{', '.join(sorted(unsafe))}",
                        frozenset({"http-methods"}),
                    )


# --- text -------------------------------------------------------------------------


# Unicode's Default_Ignorable_Code_Point property (DerivedCoreProperties.txt,
# Unicode 15.1): characters that render as nothing. Unassigned ones included.
_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_TAGS = range(0xE0000, 0xE0080)
_TAG_LETTERS = range(0xE0020, 0xE007F)
_FLAG = "\U0001f3f4"
# Tag sequences that are recommended emoji: the England, Scotland and Wales
# flags. Any other tag run, inside a flag or not, hides text.
_SUBDIVISIONS = tuple(
    "".join(chr(0xE0000 + ord(c)) for c in code) + "\U000e007f"
    for code in ("gbeng", "gbsct", "gbwls")
)
# Ignorables with a job between letters of non-Latin scripts and emoji:
# joiners and direction marks. Next to ASCII, a space or an edge they shape
# nothing.
_SHAPING = frozenset("\u200c\u200d\u200e\u200f\u061c")
# A soft hyphen marks a break point inside a word, in any script.
_SOFT_HYPHEN = "\u00ad"
_VARIATION = range(0xFE00, 0xFE10)
_VARIATION_SUPPLEMENT = range(0xE0100, 0xE01F0)
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{80,}={0,2}")
_WHITESPACE = re.compile(r"(?:\r?\n){50,}|[ \t]{200,}")


def _ignorable(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _IGNORABLE)


def _hidden_chars(text: str) -> tuple[list[str], list[str]]:
    """(tag characters, other ignorables) that serve no visible purpose.

    Every Default_Ignorable code point counts, except: a leading BOM; the
    England, Scotland and Wales flag sequences; one variation selector after
    a visible character; a variation-selector-supplement character after a CJK
    ideograph; a soft hyphen between two letters; and joiners or direction
    marks between two non-ASCII, non-space characters (emoji ZWJ sequences,
    Persian ZWNJ, Hebrew RLM).
    """

    tags: list[str] = []
    other: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == _FLAG:
            tail = next((s for s in _SUBDIVISIONS if text.startswith(s, i + 1)), None)
            if tail is not None:
                i += 1 + len(tail)
                continue
        if not _ignorable(char):
            i += 1
            continue
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        code = ord(char)
        if code in _TAGS:
            tags.append(char)
        elif char == "\ufeff" and i == 0:
            pass
        elif code in _VARIATION:
            if not before or _ignorable(before) or before.isspace():
                other.append(char)
        elif code in _VARIATION_SUPPLEMENT:
            if not unicodedata.name(before, "").startswith("CJK"):
                other.append(char)
        elif char == _SOFT_HYPHEN:
            if not (before.isalpha() and after.isalpha()):
                other.append(char)
        elif char in _SHAPING:
            if any(not c or c.isascii() or c.isspace() for c in (before, after)):
                other.append(char)
        else:
            other.append(char)
        i += 1
    return tags, other


def _decoded(run: str) -> str | None:
    """The text a base64 run decodes to, when it is mostly readable ASCII."""

    padded = run + "=" * (-len(run) % 4)
    for altchars in (None, b"-_"):
        try:
            data = base64.b64decode(padded, altchars=altchars, validate=True)
        except (binascii.Error, ValueError):
            continue
        printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in data)
        if data and printable >= 0.9 * len(data) and b" " in data:
            return data.decode("ascii", "replace")
    return None


def _comments(text: str) -> Iterator[str]:
    """HTML comment bodies; an unclosed "<!--" ends the scan."""

    start = text.find("<!--")
    while start >= 0:
        end = text.find("-->", start + 4)
        if end < 0:
            return
        yield text[start + 4 : end]
        start = text.find("<!--", end + 3)


def _codepoints(chars: list[str]) -> str:
    counts: dict[str, int] = {}
    for char in chars:
        key = f"U+{ord(char):04X}"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{n} x {cp}" for cp, n in counts.items())


def _texts(snapshot: Snapshot) -> Iterator[tuple[str, str]]:
    """(location, text) for every model-readable string a tool or the server lists."""

    for tool in _tools(snapshot):
        for key in ("name", "title", "description"):
            if isinstance(tool.get(key), str):
                yield _where(tool, key), tool[key]
        if isinstance(_annotations(tool).get("title"), str):
            yield _where(tool, "annotations.title"), _annotations(tool)["title"]
        for pointer, subschema, _ in _dict_subschemas(_schema(tool)):
            if pointer and isinstance(subschema.get("description"), str):
                yield _where(tool, f"{pointer} description"), subschema["description"]
    instructions = snapshot.initialize.get("instructions")
    if isinstance(instructions, str):
        yield "initialize instructions", instructions


def _check_hidden(snapshot: Snapshot) -> Iterator[Hit]:
    for location, text in _texts(snapshot):
        variants: set[str] = set()
        details: list[str] = []
        tags, invisible = _hidden_chars(text)
        if tags:
            hidden = "".join(
                chr(ord(c) - 0xE0000) for c in tags if ord(c) in _TAG_LETTERS
            )
            variants.add("tag-chars")
            details.append(
                f"{len(tags)} invisible Unicode tag characters (U+E0000-U+E007F) "
                f"reading {_short(hidden, 60)}"
            )
        for match in _BASE64.finditer(text):
            decoded = _decoded(match.group(0))
            if decoded is not None:
                variants.add("encoded")
                details.append(
                    f"{len(match.group(0))}-character base64 text decoding to "
                    f"{_short(decoded, 60)}"
                )
                break
        if invisible:
            variants.add("invisible")
            details.append(f"invisible characters: {_codepoints(invisible)}")
        comments = [c for c in _comments(text) if re.search(r"[^\W\d_]", c)]
        if comments:
            variants.add("html-comment")
            details.append(f"HTML comment {_short(_squash(comments[0]), 60)}")
        spaces = _WHITESPACE.search(text)
        if spaces:
            variants.add("whitespace")
            details.append(f"run of {len(spaces.group(0))} whitespace characters")
        if variants:
            yield Hit(location, "; ".join(details), frozenset(variants))


def _check_name_length(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        name = tool.get("name")
        if isinstance(name, str) and len(name) > 64:
            # Without combining characters every count of "characters" agrees.
            variant = "ascii" if name.isascii() else "non-ascii"
            yield Hit(
                _where(tool, "name"),
                f"{len(name)} code points",
                frozenset({variant}),
            )


# --- effects ----------------------------------------------------------------------


def _e(
    directory: Any,
    severity: Any,
    source: str,
    summary: str,
    rationale: str,
    when: tuple[str, ...] | None = None,
) -> DirectoryEffect:
    return DirectoryEffect(
        directory=directory,
        severity=severity,
        source=source,
        summary=summary,
        detection="exact" if severity == "error" else "heuristic",
        rationale=rationale,
        when=None if when is None else frozenset(when),
    )


_DESCRIPTION_EFFECTS = (
    _e(
        "claude",
        "warning",
        "claude-policy-define-tools",
        "may not define the tool in natural language",
        "2A asks for a natural-language definition of each tool but does not "
        "name the description field; a tool with a clear title may still "
        "satisfy it, so this is not proven.",
    ),
    _e(
        "openai",
        "error",
        "openai-description-required",
        "every tool needs a non-empty description",
        "The sentence names the field and the condition: a missing, blank or "
        "non-text description breaks it for every tool.",
    ),
)

# Omitting a description fixes the harness finding but not the directory one.
SHARED_DIRECTORY_FIXES: dict[str, str] = {
    "tool-description-not-string": "Make the description a string that says what "
    "the tool does and when to use it.",
}

SHARED_DIRECTORY_EFFECTS: dict[str, tuple[DirectoryEffect, ...]] = {
    "description-missing": _DESCRIPTION_EFFECTS,
    "tool-description-not-string": _DESCRIPTION_EFFECTS,
    "tool-name-collision": (
        _e(
            "openai",
            "error",
            "openai-unique-names",
            "tool names must be unique",
            "Two tools with the same name break the sentence directly.",
            when=("duplicate",),
        ),
    ),
}

DIRECTORY_RULES: tuple[Rule, ...] = (
    Rule(
        "directory-tool-title-missing",
        "tool has no title",
        "directory",
        (),
        'Set "title" (or annotations.title) to a short human-readable name, such '
        'as "Search flights".',
        _check_title,
        directory_effects=(
            _e(
                "claude",
                "error",
                "claude-tool-annotations",
                "every tool needs a title",
                "The sentence requires a title on every tool. Claude does not say "
                "which field, so either title or annotations.title satisfies the "
                "check; a blank title counts as none.",
            ),
        ),
    ),
    Rule(
        "directory-claude-safety-hints",
        "safety hints don't meet Claude's rule",
        "directory",
        (),
        "Set the safety hints for what the tool does.",
        _check_claude_hints,
        directory_effects=(
            _e(
                "claude",
                "error",
                "claude-tool-annotations",
                "missing the applicable safety hint",
                "Claude defines readOnlyHint as 'the tool only reads data' and "
                "destructiveHint as 'the tool can modify or delete data', so every "
                "tool falls under one of them. This tool is not marked read-only "
                "and declares no destructiveHint, so it lacks the applicable hint "
                "whichever it is; it also breaks the MCP page's 'must declare both'.",
                when=("no-applicable",),
            ),
            _e(
                "claude",
                "warning",
                "claude-declare-both-hints",
                "may need both readOnlyHint and destructiveHint declared",
                "Claude's MCP page asks for both hints on every tool, while the "
                "review criteria accept the applicable one, which this tool "
                "declares (readOnlyHint: true or destructiveHint: true); listed "
                "servers ship read-only tools without destructiveHint.",
                when=("one-declared",),
            ),
            _e(
                "claude",
                "warning",
                "claude-tool-annotations",
                "may be read-only without readOnlyHint: true",
                "destructiveHint is false and readOnlyHint is not declared. If "
                "the tool only reads, Claude's applicable hint (readOnlyHint: "
                "true) is missing; if it writes, the declared destructiveHint "
                "applies.",
                when=("read-only-undeclared",),
            ),
            _e(
                "claude",
                "warning",
                "claude-tool-annotations",
                "may need destructiveHint: true, or readOnlyHint: true if it only reads",
                "readOnlyHint false says the tool modifies data, and Claude asks "
                "for destructiveHint: true on tools that modify data. Whether an "
                "additive write such as creating a record counts is not stated, "
                "and readOnlyHint false may itself be wrong for a tool that only "
                "returns a link.",
                when=("write-not-destructive",),
            ),
        ),
        fixes=(
            (
                "no-applicable",
                'Set "readOnlyHint": true if this tool only reads; otherwise set '
                '"destructiveHint" (true if it modifies or deletes data). Claude\'s '
                "MCP page asks for both hints on every tool.",
            ),
            (
                "one-declared",
                'Declare both "readOnlyHint" and "destructiveHint" as booleans; a '
                'tool with "destructiveHint": true takes "readOnlyHint": false.',
            ),
            (
                "read-only-undeclared",
                'Set "readOnlyHint": true if this tool only reads, otherwise '
                '"readOnlyHint": false.',
            ),
            (
                "write-not-destructive",
                'If this tool only reads, set "readOnlyHint": true. Otherwise set '
                '"destructiveHint": true unless it only adds new records; when '
                "unsure, choose true, which makes Claude ask before each call.",
            ),
        ),
    ),
    Rule(
        "directory-hints-not-explicit",
        "safety hints not explicit booleans",
        "directory",
        (),
        'Set "readOnlyHint", "destructiveHint" and "openWorldHint" to true or false '
        'in annotations; don\'t rely on defaults. "openWorldHint" is true when the '
        "tool reaches the public internet or open-ended entities (web search, "
        "arbitrary recipients) and false when it stays within the user's own "
        "account, workspace or catalog.",
        _check_explicit_hints,
        directory_effects=(
            _e(
                "openai",
                "error",
                "openai-explicit-hints",
                "all three hints must be explicit booleans",
                "The sentence requires all three hints as booleans on every tool; "
                "a missing or non-boolean hint breaks it directly.",
            ),
        ),
    ),
    Rule(
        "directory-hints-contradict",
        "tool marked both read-only and destructive",
        "directory",
        (),
        'If the tool only reads, set "destructiveHint": false; if it changes '
        'anything, set "readOnlyHint": false.',
        _check_contradictory_hints,
        directory_effects=(
            _e(
                "openai",
                "error",
                "openai-hints-exclusive",
                "readOnlyHint and destructiveHint cannot both be true",
                "If the tool is read-only, the destructiveHint line requires "
                "false; if it changes anything, the readOnlyHint line requires "
                "false. Either way one of the two hints breaks a rule.",
            ),
        ),
    ),
    Rule(
        "directory-read-write-tool",
        "one tool may combine reads and writes",
        "directory",
        (),
        "Split reads and writes into separate tools.",
        _check_read_write,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-separate-read-write",
                "may combine safe and unsafe HTTP methods in one tool, which Claude rejects",
                "The method input lists both safe and unsafe methods, which "
                "Claude rejects. The schema can't show whether the server refuses "
                "some of them at run time.",
                when=("http-methods",),
            ),
            _e(
                "openai",
                "warning",
                "openai-separate-operations",
                "may expose several operations through one tool",
                "OpenAI asks for each operation as a separate tool and forbids "
                "operation selection through a generic executor; the allowed "
                "values suggest that, which the schema alone cannot confirm.",
                when=("http-methods",),
            ),
        ),
        fixes=(
            (
                "http-methods",
                "Split this into a read-only tool for GET/HEAD/OPTIONS and one tool "
                "per write action (for example create, update, delete), each with "
                "its own title and hints.",
            ),
        ),
    ),
    Rule(
        "directory-hidden-content",
        "text contains hidden or encoded content",
        "directory",
        (),
        "Put all guidance in plain, visible text.",
        _check_hidden,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-policy-hidden",
                "contains content that may hide instructions",
                "Invisible characters, encoded text, comments or long blank runs "
                "are present; whether they carry instructions is a judgment the "
                "check cannot make.",
            ),
            _e(
                "openai",
                "warning",
                "openai-user-intent",
                "contains content that may conceal behaviour",
                "Invisible characters, encoded text, comments or long blank runs "
                "are present; encoding alone is no breach, and whether they "
                "conceal behaviour is a judgment the check cannot make.",
            ),
        ),
        fixes=(
            ("tag-chars", "Remove the invisible Unicode tag characters."),
            (
                "encoded",
                "Replace the base64 text with the plain text it encodes, or remove it.",
            ),
            ("invisible", "Remove the invisible formatting characters."),
            ("html-comment", "Remove the HTML comment or make its text visible."),
            ("whitespace", "Remove the long run of blank space."),
        ),
    ),
    Rule(
        "directory-tool-name-too-long",
        "tool name over 64 characters",
        "directory",
        (),
        "Shorten the tool name to 64 characters or fewer.",
        _check_name_length,
        directory_effects=(
            _e(
                "claude",
                "error",
                "claude-tool-name-length",
                "tool names must be 64 characters or fewer",
                "The name is ASCII and over 64 characters, so every way of "
                "counting characters gives more than 64.",
                when=("ascii",),
            ),
            _e(
                "claude",
                "warning",
                "claude-tool-name-length",
                "may be over 64 characters",
                "The name has more than 64 code points; with combining characters "
                "it may have 64 or fewer visible characters.",
                when=("non-ascii",),
            ),
        ),
    ),
)
