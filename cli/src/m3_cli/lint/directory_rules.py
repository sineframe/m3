"""Directory readiness checks: server metadata against published directory rules.

Each effect cites a verbatim policy sentence from ``directories.SOURCES``. An
effect is an error only when every tool matching its condition breaks that
sentence, whatever the tool does; otherwise it is a warning whose summary
says "may". The word and phrase lists below are closed: a term not listed is
not matched.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterator
from itertools import pairwise
from typing import Any

from .collect import Snapshot
from .directories import DirectoryEffect
from .rules import Hit, Rule, _dict_subschemas, _schema, _short, _tools, _types, _where
from .schema import iter_property_maps

# --- closed lists -------------------------------------------------------------

WRITE_VERBS = frozenset(
    "create add insert update edit modify set delete remove send post publish "
    "upload cancel archive move submit pay refund transfer invite deploy "
    "execute revoke approve write rename merge close reply forward purchase "
    "buy".split()
)
READ_VERBS = frozenset(
    "get list search find read fetch query describe show lookup count view "
    "preview retrieve check browse".split()
)
# OpenAI's destructiveHint sentence: deletion, overwriting, cancellation,
# access revocation, irreversible sends or transactions.
IRREVERSIBLE_VERBS = frozenset(
    "delete remove drop purge erase destroy overwrite cancel revoke send "
    "publish pay refund transfer".split()
)
# Claude's sentence covers tools that "modify or delete data".
DELETE_VERBS = frozenset("delete remove drop purge erase destroy overwrite".split())
RESTORE_PLACES = frozenset({"trash", "spam", "bin", "archive"})
# Leading words that are as often nouns ("post_list", "set_members"): with a
# verb right after them, the second word is the action.
NOUN_LIKE = frozenset("post set list query view check count show".split())

HTTP_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
HTTP_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HTTP_METHODS = HTTP_SAFE | HTTP_UNSAFE | {"TRACE", "CONNECT"}
METHOD_NAMES = frozenset({"method", "httpmethod", "requestmethod"})
TARGET_NAMES = frozenset({"path", "url", "endpoint", "uri", "route"})
ACTION_NAMES = frozenset({"action", "operation", "operationtype", "op"})

# Inputs that only free-form API tools take.
FREEFORM_NAMES = frozenset(
    "endpoint urlpath apipath sql sqlquery graphql graphqlquery gql "
    "querystring rawquery".split()
)
# Inputs that are free-form API input only next to a method or endpoint; a
# body alone may go to a fixed endpoint, which needs no API reference.
FREEFORM_WITH_METHOD = frozenset({"path", "route", "body", "requestbody", "query"})
QUERY_LANGUAGE = re.compile(r"\b(?:SQL|GraphQL|query language)\b", re.IGNORECASE)
# In a tool description, the words must describe a query, not a topic.
QUERY_PHRASE = re.compile(
    r"\b(?:SQL|GraphQL) (?:query|queries|statements?)\b|\bquery language\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S")
_API = re.compile(r"\bAPIs?\b")
# Matched against the text just before "API", so the search stays linear.
_API_NAME = re.compile(r"(?:\b[A-Z][\w.&+-]*(?:['\u2019]s)? ){1,4}$")
# Capitalised words that describe an API without naming it.
_GENERIC_API_WORDS = frozenset(
    "the this that a an our your its their rest web graphql http https json "
    "admin public private internal open".split()
)


def _names_api(text: str) -> bool:
    """A link, or a capitalised name followed by API ("Slack Web API")."""

    if _URL.search(text):
        return True
    for m in _API.finditer(text):
        name = _API_NAME.search(text[max(0, m.start() - 80) : m.start()])
        words = name.group(0).split() if name else []
        if any(
            re.sub(r"['\u2019]s$", "", w).lower() not in _GENERIC_API_WORDS
            for w in words
        ):
            return True
    return False


HISTORY_NAMES = frozenset(
    "conversation conversationhistory chathistory history messages transcript "
    "chattranscript priorturns previousmessages context conversationcontext".split()
)
CREDENTIAL_NAMES = frozenset(
    "password passwd passphrase apikey apisecret clientsecret secretkey "
    "privatekey accesstoken refreshtoken bearertoken authtoken otp otpcode "
    "totp totpcode mfacode 2facode verificationcode".split()
)
CARD_NAMES = frozenset(
    "cardnumber creditcardnumber ccnumber cvv cvc cvv2 cardcvv cardcvc".split()
)
GOV_ID_NAMES = frozenset(
    "ssn socialsecuritynumber passportnumber nationalid nationalidnumber "
    "driverslicensenumber".split()
)
ADDRESS_NAMES = frozenset(
    "streetaddress homeaddress useraddress postaladdress mailingaddress "
    "shippingaddress billingaddress".split()
)
LATITUDE_NAMES = frozenset({"lat", "latitude"})
LONGITUDE_NAMES = frozenset({"lng", "lon", "long", "longitude"})


def _phrases(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_NOT = r"(?:do not|don['\u2019]t|never)"
_BUILT_IN = r"(?:built-in|builtin|default|fallback|native)"
STEERING: dict[str, tuple[re.Pattern[str], ...]] = {
    "exclusivity": _phrases(
        r"\bfirst and only (?:tool|choice)\b",
        r"\bonly tool (?:you|to) (?:should|use|need)\b",
        r"\bpreferred (?:\w+ ){0,2}tool\b",
        # Not about web search or built-ins, which avoid-builtins covers.
        r"\buse this tool instead of (?!(?:a |the )?(?:web search|searching the web|built-in|the browser))",
    ),
    "avoid-other-tools": _phrases(
        rf"\b{_NOT} (?:use|call|invoke) (?:any )?other (?:\w+ ){{0,3}}tools?\b",
        r"\binstead of (?:any |the )?other tools\b",
        r"\bprefer this (?:\w+ )?over (?:any |the )?other\b",
    ),
    "avoid-builtins": _phrases(
        rf"\b{_NOT} (?:use|call|invoke) (?:any )?{_BUILT_IN} (?:or {_BUILT_IN} )?(?:\w+ ){{0,3}}tools?\b",
        rf"\b{_NOT} (?:also )?(?:use|call|run|do|perform|rely on|fall back to) (?:a |the )?(?:web search(?:es)?|browser|browsing)\b",
        rf"\b{_NOT} (?:also )?(?:\w+ [\w-]+ or )?search the web\b",
        r"\b(?:skip|avoid) (?:a |the |any )?(?:web search(?:es)?|searching the web|browsing|using the browser)\b",
        r"\binstead of (?:a |the )?(?:web search|built-in|searching the web)\b",
        r"\bprefer this (?:\w+ )?over (?:a |the )?(?:web search|built-in)\b",
    ),
    "chaining": _phrases(
        r"\bafter every (?:response|message|answer|turn|reply|tool call)\b",
        r"\bat the start of (?:every|each|new|all) (?:\w+ )?conversations?\b",
        r"\bas the (?:final|last) step\b",
        r"\bbefore (?:doing )?anything else\b",
        r"\bbefore proceeding with (?:any )?other tasks?\b",
        r"\bpage through all\b",
        r"\b(?:retrieve|fetch|get) all \w+ before\b",
    ),
    "override": _phrases(
        r"\b(?:ignore|disregard) (?:all |any )?(?:previous|prior|above|earlier|system) (?:instructions|rules|prompts?)\b",
        r"\b(?:ignore|disregard) (?:your|the) (?:system prompt|instructions)\b",
    ),
    "concealment": _phrases(
        rf"\b{_NOT} (?:tell|inform|reveal to) the user (?:that|which|what|how|about (?:this|the) tool)\b",
        r"\bwithout the user (?:knowing|noticing)\b",
        r"\bregardless of what the user (?:says|asks|wants)\b",
    ),
    "promotion": _phrases(
        r"(?<![\w#])(?:the )?(?:best|#1|number one|most accurate|most reliable|most trusted|leading) (?!(?:matching|match|fit|fitting|available|possible|suited)\b)(?:\w+ ){0,2}(?:tool|source|service|provider)s?\b",
    ),
}

# Between the verb and what it moves: determiners and amounts only, so "send
# an email with a cash flow report" is not money movement.
_AMOUNT = r"(?:(?:the|a|an|some|your|their|our|my|\$?[\d,.]+|usd|eur|gbp) ){0,2}"
MONEY_MOVEMENT = _phrases(
    rf"\b(?:transfer|send|wire|move|withdraw|deposit) {_AMOUNT}(?:money|funds|cash|crypto(?:currency)?|bitcoin|btc|ether|eth|usdc|stablecoins?)\b",
    r"\b(?:initiate|create|make|execute|submit|send) (?:an? |the )?(?:\w+ )?(?:payout|wire transfer|bank transfer|money transfer|ach transfer)s?\b",
)
TRADING = _phrases(
    r"\b(?:place|submit|execute|create) (?:an? |the )?(?:\w+ ){0,2}?(?:trades?|stock orders?|market orders?|limit orders?|buy orders?|sell orders?)\b",
    rf"\b(?:buy|sell) {_AMOUNT}(?:shares|stocks|options contracts?|crypto(?:currency)?|bitcoin|etfs?)\b",
)
AI_MEDIA = _phrases(
    r"\bgenerat(?:e|es|ing) (?:an? |the )?(?:\w+ ){0,2}?(?:images?|videos?|audio|music|songs?|voiceovers?|voices?|speech|photos?|illustrations?)\b",
    r"\btext[- ]to[- ](?:image|video|speech|audio|music)\b",
)
# Claude excludes media made "through AI models"; without a sign of a model the
# phrase may be a QR code or a thumbnail.
AI_CONTEXT = re.compile(
    r"\b(?:AI|generative|neural|diffusion|DALL-E|Midjourney|Imagen|Sora"
    r"|text[- ]to[- ]\w+|(?:image|video|audio|speech|language) models?"
    r"|from (?:a |the |your )?(?:text )?prompt)\b",
    re.IGNORECASE,
)
DESIGN_CONTEXT = re.compile(
    r"\b(?:diagrams?|charts?|slides?|mockups?|logos?|wireframes?)\b", re.IGNORECASE
)

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


def _texts(snapshot: Snapshot) -> Iterator[tuple[str, str, str]]:
    """(location, scope, text) for model-readable prose.

    Scope is "tool" for a tool's title and description, "input" for input
    property descriptions and "instructions" for the server instructions.
    """

    for tool in _tools(snapshot):
        for key in ("title", "description"):
            if isinstance(tool.get(key), str):
                yield _where(tool, key), "tool", tool[key]
        if isinstance(_annotations(tool).get("title"), str):
            yield _where(tool, "annotations.title"), "tool", _annotations(tool)["title"]
        for pointer, subschema, _ in _dict_subschemas(_schema(tool)):
            if pointer and isinstance(subschema.get("description"), str):
                yield (
                    _where(tool, f"{pointer} description"),
                    "input",
                    subschema["description"],
                )
    instructions = snapshot.initialize.get("instructions")
    if isinstance(instructions, str):
        yield "initialize instructions", "instructions", instructions


def _properties(tool: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(pointer, name, schema) for every named input property."""

    for pointer, properties in iter_property_maps(_schema(tool)):
        for name, prop in properties.items():
            if isinstance(prop, dict):
                yield f"{pointer}/{name}", name, prop


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
        elif not isinstance(read_only, bool):
            variant = "read-only-undeclared"
        elif not isinstance(destructive, bool):
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


def _other_action(words: list[str], verbs: frozenset[str]) -> bool:
    """True when the name's action may be a later verb: a noun-like first word
    followed by a verb ("post_list"), or verbs joined ("get_or_create")."""

    if len(words) > 1 and words[0] in NOUN_LIKE and words[1] in verbs:
        return True
    return any(a in ("or", "and") and b in verbs for a, b in pairwise(words[1:]))


def _check_name_hints(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        name = tool.get("name")
        if not isinstance(name, str):
            continue
        title = _title(tool)
        leads = [("name", _tokens(name)), ("title", _tokens(title) if title else [])]
        annotations = _annotations(tool)
        read_only = annotations.get("readOnlyHint")
        destructive = annotations.get("destructiveHint")
        variants: set[str] = set()
        details: list[str] = []
        same_verb = leads[0][1][:1] == leads[1][1][:1]
        for field, words in leads:
            if not words or (field == "title" and same_verb):
                continue
            field = "name and title" if same_verb else field
            verb = words[0]
            # "Remove message from Trash" restores the item; it deletes nothing.
            restores = verb == "remove" and any(
                a == "from" and b in RESTORE_PLACES for a, b in pairwise(words)
            )
            if (
                verb in WRITE_VERBS
                and read_only is True
                and not _other_action(words, READ_VERBS)
            ):
                variants.add("write-marked-read")
                details.append(f"{field} starts with {verb!r}; readOnlyHint true")
            if (
                verb in READ_VERBS
                and destructive is True
                and not _other_action(words, WRITE_VERBS)
            ):
                variants.add("read-marked-destructive")
                details.append(f"{field} starts with {verb!r}; destructiveHint true")
            if (
                verb in IRREVERSIBLE_VERBS
                and destructive is False
                and not _other_action(words, READ_VERBS)
                and not restores
            ):
                variants.add("irreversible-not-destructive")
                if verb in DELETE_VERBS:
                    variants.add("delete-not-destructive")
                details.append(f"{field} starts with {verb!r}; destructiveHint false")
        if variants:
            yield Hit(
                _where(tool, "annotations"),
                "; ".join(dict.fromkeys(details)),
                frozenset(variants),
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


def _leading_verbs(values: set[str], verbs: frozenset[str]) -> list[str]:
    return sorted(v for v in values if _tokens(v)[:1] and _tokens(v)[0] in verbs)


def _check_read_write(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        for pointer, props in _objects(tool):
            names = {_norm(name) for name in props}
            for name, prop in props.items():
                where = _where(tool, f"{pointer}/properties/{name}")
                values = _values(prop)
                key = _norm(name)
                if values is not None:
                    upper = {v.upper() for v in values}
                    safe, unsafe = upper & HTTP_SAFE, upper & HTTP_UNSAFE
                    if (
                        key in METHOD_NAMES
                        and safe
                        and unsafe
                        and upper <= HTTP_METHODS
                    ):
                        yield Hit(
                            where,
                            f"accepts safe {', '.join(sorted(safe))} and unsafe "
                            f"{', '.join(sorted(unsafe))}",
                            frozenset({"http-methods"}),
                        )
                    reads = _leading_verbs(values, READ_VERBS)
                    writes = _leading_verbs(values, WRITE_VERBS)
                    if key in ACTION_NAMES and reads and writes:
                        yield Hit(
                            where,
                            f"reads {_short(reads[:3], 60)}, "
                            f"writes {_short(writes[:3], 60)}",
                            frozenset({"mixed-actions"}),
                        )
                elif (
                    key in METHOD_NAMES
                    and "string" in _types(prop.get("type"))
                    and "pattern" not in prop
                    and names & TARGET_NAMES
                ):
                    yield Hit(
                        where,
                        "free-form method string next to a path or URL input",
                        frozenset({"http-method-free"}),
                    )


_HTTP_WORDS = re.compile(r"\b(?:HTTP|GET|POST|PUT|PATCH|DELETE)\b")


def _takes_http_methods(name: str, prop: dict[str, Any]) -> bool:
    values = _values(prop)
    if values is not None:
        # Only HTTP methods: an action enum may also contain "delete".
        return bool(values) and {v.upper() for v in values} <= HTTP_METHODS
    # A free string named just "method" may pick an action (add, reply).
    description = prop.get("description")
    return "string" in _types(prop.get("type")) and (
        _norm(name) != "method"
        or (isinstance(description, str) and bool(_HTTP_WORDS.search(description)))
    )


def _check_query_docs(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        schema = _schema(tool)
        properties = schema.get("properties") if schema else None
        if not isinstance(properties, dict):
            continue
        description = tool.get("description")
        description = description if isinstance(description, str) else ""
        names = {_norm(n): n for n, p in properties.items() if isinstance(p, dict)}
        # A method input anchors free-form API input only when it takes HTTP
        # methods; an enum of actions (create, update) does not.
        http_method = any(
            key in METHOD_NAMES and _takes_http_methods(name, properties[name])
            for key, name in names.items()
        )
        anchored = http_method or any(
            key in FREEFORM_NAMES
            and "enum" not in properties[name]
            and "const" not in properties[name]
            for key, name in names.items()
        )
        query_phrase = bool(QUERY_PHRASE.search(description))
        freeform = []
        for key, name in names.items():
            prop = properties[name]
            if "enum" in prop or "const" in prop:
                continue
            own = prop.get("description")
            describes_query = query_phrase or (
                isinstance(own, str) and bool(QUERY_LANGUAGE.search(own))
            )
            if key in FREEFORM_NAMES:
                freeform.append(name)
            elif key in FREEFORM_WITH_METHOD and (
                anchored or (key == "query" and describes_query)
            ):
                freeform.append(name)
        if freeform and not _names_api(description):
            yield Hit(
                _where(tool, "description"),
                f"free-form input {', '.join(map(repr, sorted(freeform)))}; "
                "description has no link and no '<Name> API'",
            )


# --- text -------------------------------------------------------------------------


def _check_steering(snapshot: Snapshot) -> Iterator[Hit]:
    for location, scope, text in _texts(snapshot):
        flat = _squash(text)
        found: list[str] = []
        variants: set[str] = set()
        for kind, patterns in STEERING.items():
            for pattern in patterns:
                match = pattern.search(flat)
                if match:
                    variants.add(f"{kind}:{scope}")
                    found.append(f"{kind}: {match.group(0)!r}")
                    break
        if variants:
            yield Hit(location, "; ".join(found), frozenset(variants))


_TAG = range(0xE0020, 0xE0080)
_TAG_LETTERS = range(0xE0020, 0xE007F)
_FLAG = "\U0001f3f4"
# Tag sequences that are recommended emoji: the England, Scotland and Wales
# flags. Any other tag run, inside a flag or not, hides text.
_SUBDIVISIONS = tuple(
    "".join(chr(0xE0000 + ord(c)) for c in code) + "\U000e007f"
    for code in ("gbeng", "gbsct", "gbwls")
)
_CANCEL_TAG = "\U000e007f"
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Invisible characters with no use in tool text: zero-width space, word
# joiner, invisible math operators, Mongolian vowel separator, Hangul fillers.
_ZERO_WIDTH = frozenset(
    "\u200b\u2060\u2061\u2062\u2063\u2064\u180e\u115f\u1160\u3164\uffa0\ufeff"
)
# Joiners and direction marks, legitimate between non-ASCII letters.
_JOINERS = frozenset("\u200c\u200d\u200e\u200f\u061c")
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{80,}={0,2}")
_WHITESPACE = re.compile(r"(?:\r?\n){50,}|[ \t]{200,}")


def _stray_tags(text: str) -> list[str]:
    """Tag characters outside emoji tag sequences (U+1F3F4, tags, U+E007F)."""

    stray: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == _FLAG:
            tail = next((s for s in _SUBDIVISIONS if text.startswith(s, i + 1)), None)
            if tail is not None:
                i += 1 + len(tail)
                continue
        if char == "\U000e0001" or ord(char) in _TAG:
            stray.append(char)
        i += 1
    return stray


def _invisible(text: str) -> list[str]:
    found: list[str] = []
    run = 0
    for i, char in enumerate(text):
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if char in _BIDI or (char in _ZERO_WIDTH and not (char == "\ufeff" and i == 0)):
            found.append(char)
        elif char in _JOINERS and any(
            not c or c.isascii() or c.isspace() for c in (before, after)
        ):
            found.append(char)
        elif 0xE0100 <= ord(char) <= 0xE01EF and not (
            before and unicodedata.name(before, "").startswith("CJK")
        ):
            found.append(char)
        run = run + 1 if 0xFE00 <= ord(char) <= 0xFE0F else 0
        if run == 2:
            found.extend([char, char])
        elif run > 2:
            found.append(char)
    return found


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


def _check_hidden(snapshot: Snapshot) -> Iterator[Hit]:
    def every_text() -> Iterator[tuple[str, str]]:
        for tool in _tools(snapshot):
            if isinstance(tool.get("name"), str):
                yield _where(tool, "name"), tool["name"]
        for location, _, text in _texts(snapshot):
            yield location, text

    for location, text in every_text():
        variants: set[str] = set()
        details: list[str] = []
        tags = _stray_tags(text)
        if tags:
            hidden = "".join(
                chr(ord(c) - 0xE0000) for c in tags if ord(c) in _TAG_LETTERS
            )
            variants.add("tag-chars")
            details.append(
                f"{len(tags)} invisible Unicode tag characters (U+E0001-U+E007F) "
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
        invisible = _invisible(text)
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


# --- inputs ---------------------------------------------------------------------


def _check_history_inputs(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        for pointer, name, prop in _properties(tool):
            types = _types(prop.get("type"))
            items = prop.get("items")
            variants: set[str] = set()
            details: list[str] = []
            if isinstance(items, dict) and isinstance(items.get("properties"), dict):
                keys = items["properties"].keys()
                if "role" in keys and "content" in keys:
                    variants.add("prior-turns")
                    details.append("array of {role, content} objects")
            if _norm(name) in HISTORY_NAMES and {"array", "object"} & set(types):
                variants.add("named")
                details.append(f"{name!r} takes {'/'.join(types)}")
            if variants:
                yield Hit(
                    _where(tool, pointer), "; ".join(details), frozenset(variants)
                )


def _check_restricted_inputs(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        for pointer, name, prop in _properties(tool):
            key = _norm(name)
            variant = None
            if key in CREDENTIAL_NAMES or prop.get("format") == "password":
                variant = "credential"
            elif key in CARD_NAMES:
                variant = "payment-card"
            elif key in GOV_ID_NAMES:
                variant = "government-id"
            elif key in ADDRESS_NAMES:
                variant = "address"
            if variant:
                yield Hit(
                    _where(tool, pointer), f"input {name!r}", frozenset({variant})
                )
        for pointer, properties in iter_property_maps(_schema(tool)):
            keys = {_norm(n) for n in properties}
            if keys & LATITUDE_NAMES and keys & LONGITUDE_NAMES:
                yield Hit(
                    _where(tool, pointer),
                    "latitude and longitude inputs",
                    frozenset({"coordinates"}),
                )


def _check_use_cases(snapshot: Snapshot) -> Iterator[Hit]:
    for tool in _tools(snapshot):
        texts = (
            [" ".join(_tokens(tool["name"]))]
            if isinstance(tool.get("name"), str)
            else []
        )
        texts += [
            t
            for t in (tool.get("title"), tool.get("description"))
            if isinstance(t, str)
        ]
        text = _squash(" . ".join(texts))
        read_only = _annotations(tool).get("readOnlyHint") is True
        variants: set[str] = set()
        details: list[str] = []
        checks: list[tuple[str, tuple[re.Pattern[str], ...]]] = []
        if not read_only:
            # A read-only tool reports on money or orders; it moves neither.
            checks += [("money-movement", MONEY_MOVEMENT), ("trading", TRADING)]
        if AI_CONTEXT.search(text) and not DESIGN_CONTEXT.search(text):
            checks.append(("ai-media", AI_MEDIA))
        for variant, patterns in checks:
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    variants.add(variant)
                    details.append(f"{variant}: {match.group(0)!r}")
                    break
        if variants:
            yield Hit(_where(tool), "; ".join(details), frozenset(variants))


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


def _scoped(kinds: tuple[str, ...], scopes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{kind}:{scope}" for kind in kinds for scope in scopes)


_TOOL_TEXT = ("tool", "input")
_STEER_AWAY = ("exclusivity", "avoid-other-tools", "avoid-builtins")
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
                "declares; listed servers ship read-only tools without "
                "destructiveHint.",
                when=("one-declared",),
            ),
            _e(
                "claude",
                "warning",
                "claude-tool-annotations",
                "may be read-only without readOnlyHint: true",
                "destructiveHint is declared but readOnlyHint is not. If the tool "
                "only reads, Claude's applicable hint (readOnlyHint: true) is "
                "missing; if it writes, the declared destructiveHint applies.",
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
                'Declare both "readOnlyHint" and "destructiveHint" as booleans.',
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
        "directory-name-contradicts-hints",
        "tool name suggests other safety hints",
        "directory",
        (),
        "Check the hints against what the tool does.",
        _check_name_hints,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-declare-both-hints",
                "may be a write tool marked read-only",
                "The name or title starts with a writing verb, while Claude "
                "defines readOnlyHint as 'the tool only reads data'. The verb may "
                "not describe what the tool does, for example a 'set' that only "
                "previews.",
                when=("write-marked-read",),
            ),
            _e(
                "openai",
                "warning",
                "openai-read-only-hint",
                "may be a write tool marked read-only",
                "The name or title starts with a writing verb and OpenAI asks for "
                "readOnlyHint: false on writes; the verb may not describe what "
                "the tool does.",
                when=("write-marked-read",),
            ),
            _e(
                "openai",
                "warning",
                "openai-read-only-not-destructive",
                "may be a read-only tool marked destructive",
                "The name or title starts with a reading verb and OpenAI asks for "
                "destructiveHint: false on read-only tools; the tool may still "
                "write despite its name.",
                when=("read-marked-destructive",),
            ),
            _e(
                "claude",
                "warning",
                "claude-tool-annotations",
                "may delete data without destructiveHint: true",
                "The name or title starts with a deleting or overwriting verb and "
                "Claude asks for destructiveHint: true on tools that modify or "
                "delete data; the verb may not describe what the tool does.",
                when=("delete-not-destructive",),
            ),
            _e(
                "openai",
                "warning",
                "openai-destructive-hint",
                "may be destructive but marked non-destructive",
                "The name or title starts with a verb from OpenAI's list of "
                "destructive or irreversible effects. OpenAI qualifies sends as "
                "irreversible ones, and the tool may not do what the verb says.",
                when=("irreversible-not-destructive",),
            ),
        ),
        fixes=(
            (
                "write-marked-read",
                'If this tool changes anything, set "readOnlyHint": false and set '
                '"destructiveHint" for its effect; if it only reads, ignore this.',
            ),
            (
                "read-marked-destructive",
                'If this tool only reads, set "destructiveHint": false; if it also '
                "writes, ignore this.",
            ),
            (
                "irreversible-not-destructive",
                "OpenAI counts deleting, overwriting, cancelling, revoking access "
                "and irreversible sends or payments as destructive: if the tool "
                'does one of these, set "destructiveHint": true, even if an undo '
                "exists.",
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
                "claude",
                "warning",
                "claude-separate-read-write",
                "may be a catch-all API tool with a method parameter",
                "Claude names a catch-all tool with a method parameter as "
                "rejected; a free method string next to a path or URL suggests "
                "one, though the server may restrict methods itself.",
                when=("http-method-free",),
            ),
            _e(
                "claude",
                "warning",
                "claude-split-catch-all",
                "may combine read and write operations in one tool",
                "The action values start with both reading and writing verbs, "
                "which Claude asks to split; the sentence is written about HTTP "
                "catch-alls, and the verbs may not describe each action.",
                when=("mixed-actions",),
            ),
            _e(
                "openai",
                "warning",
                "openai-separate-operations",
                "may expose several operations through one tool",
                "OpenAI asks for each operation as a separate tool and forbids "
                "operation selection through a generic executor; the allowed "
                "values suggest that, which the schema alone cannot confirm.",
                when=("http-methods", "http-method-free", "mixed-actions"),
            ),
        ),
        fixes=(
            (
                "http-methods",
                "Split this into a read-only tool for GET/HEAD/OPTIONS and one tool "
                "per write action (for example create, update, delete), each with "
                "its own title and hints.",
            ),
            (
                "http-method-free",
                "Replace the free method string with separate read and write tools, "
                "or limit it with an enum of read-only methods.",
            ),
            (
                "mixed-actions",
                "Split the read actions and the write actions into separate tools, "
                "each with its own title and hints.",
            ),
        ),
    ),
    Rule(
        "directory-query-tool-without-api-docs",
        "free-form API tool without API docs",
        "directory",
        (),
        "Name the target API or link its docs in the description, for example "
        '"Queries the Slack Web API, see https://api.slack.com/methods".',
        _check_query_docs,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-query-api-docs",
                "may need a link to or the name of the target API",
                "The tool takes a free-form path, query or body, and the "
                "description has no URL and no '<Name> API' phrase. It may name "
                "the target another way (such as 'your PostgreSQL database'), "
                "which this check cannot recognise.",
            ),
        ),
    ),
    Rule(
        "directory-steering-text",
        "text may steer the model",
        "directory",
        (),
        "Describe what the tool does and when it's useful.",
        _check_steering,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-no-interference",
                "may interfere with Claude calling other tools, which Claude rejects",
                "The phrase tells the model to prefer this tool or avoid others, "
                "including web search or built-in tools; in context it may only "
                "point to a sibling tool of the same server.",
                when=_scoped(_STEER_AWAY, _TOOL_TEXT),
            ),
            _e(
                "claude",
                "warning",
                "claude-no-unrequested-calls",
                "may make Claude call tools the user didn't request, which Claude rejects",
                "The phrase asks for calls on every turn, as a final step, before "
                "other work or across every page; in context it may describe a "
                "required step.",
                when=_scoped(("chaining",), _TOOL_TEXT),
            ),
            _e(
                "claude",
                "warning",
                "claude-no-override",
                "may try to override system instructions, which Claude rejects",
                "The phrase asks the model to ignore instructions or rules; it "
                "may be quoted as an example, for instance by a security tool.",
                when=_scoped(("override",), _TOOL_TEXT),
            ),
            _e(
                "claude",
                "warning",
                "claude-no-promotion",
                "may promote a product or service, which Claude rejects",
                "The phrase ranks the tool or its source above others; it may be "
                "a factual statement, such as finding the best provider for the "
                "user.",
                when=_scoped(("promotion",), _TOOL_TEXT),
            ),
            _e(
                "claude",
                "warning",
                "claude-policy-interfere",
                "may interfere with Claude calling other tools",
                "Server instructions telling the model to prefer this server or "
                "avoid other tools may interfere with them; the phrase may only "
                "route between the server's own tools.",
                when=_scoped(_STEER_AWAY, ("instructions",)),
            ),
            _e(
                "claude",
                "warning",
                "claude-policy-coerce",
                "may make Claude call tools the user didn't request",
                "Server instructions asking for calls on every turn or before "
                "other work may coerce unrequested calls; in context they may "
                "describe a required step.",
                when=("chaining:instructions",),
            ),
            _e(
                "claude",
                "warning",
                "claude-policy-guardrails",
                "may try to override system instructions",
                "Server instructions asking the model to ignore instructions or "
                "rules may circumvent system instructions; the phrase may be "
                "quoted as an example.",
                when=("override:instructions",),
            ),
            _e(
                "openai",
                "warning",
                "openai-fair-play",
                "may steer the model away from other plugins' tools",
                "The phrase tells the model to prefer this tool or avoid other "
                "tools; it may be harmless in context. Phrases about web search "
                "or built-in tools are not counted, since the sentence covers "
                "other plugins.",
                when=_scoped(
                    ("exclusivity", "avoid-other-tools"), (*_TOOL_TEXT, "instructions")
                ),
            ),
            _e(
                "openai",
                "warning",
                "openai-broad-triggering",
                "may ask for calls beyond what the user asked",
                "The phrase asks for calls on every turn, as a final step, before "
                "other work or across every page; in context it may describe a "
                "required step.",
                when=_scoped(("chaining",), _TOOL_TEXT),
            ),
            _e(
                "openai",
                "warning",
                "openai-user-intent",
                "may override instructions or conceal behaviour",
                "The phrase asks the model to ignore instructions, keep something "
                "from the user or disregard what the user says; it may be quoted "
                "as an example.",
                when=_scoped(
                    ("override", "concealment"), (*_TOOL_TEXT, "instructions")
                ),
            ),
            _e(
                "muse",
                "warning",
                "muse-tool-descriptions",
                "may steer Muse away from competitors",
                "The phrase tells the model to prefer this tool or avoid other "
                "tools, which may steer it away from competitors; phrases about "
                "web search or built-in tools are not counted.",
                when=("exclusivity:tool", "avoid-other-tools:tool"),
            ),
            _e(
                "muse",
                "warning",
                "muse-tool-descriptions",
                "may make Muse take unrequested actions",
                "The phrase asks for calls on every turn, as a final step or "
                "before other work; in context it may describe a required step.",
                when=("chaining:tool",),
            ),
            _e(
                "muse",
                "warning",
                "muse-tool-descriptions",
                "may tell Muse to ignore its rules",
                "The phrase asks the model to ignore instructions or rules; it "
                "may be quoted as an example.",
                when=("override:tool",),
            ),
        ),
        fixes=(
            (
                "exclusivity",
                "Drop claims that this is the preferred or only tool; say when it "
                "helps.",
            ),
            ("avoid-other-tools", "Remove the instruction not to use other tools."),
            (
                "avoid-builtins",
                "Remove the instruction not to use web search, the browser or "
                "built-in tools.",
            ),
            (
                "chaining",
                "Remove the instruction to call it on every turn or before other "
                "work; say when it is useful.",
            ),
            (
                "override",
                "Remove the instruction to ignore other instructions or rules.",
            ),
            (
                "concealment",
                "Remove the instruction to keep things from the user or disregard "
                "what the user says.",
            ),
            ("promotion", "Replace the ranking claim with what the tool returns."),
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
    Rule(
        "directory-conversation-history-input",
        "input may ask for conversation history",
        "directory",
        (),
        "Ask only for the task-specific input, such as a query or one message.",
        _check_history_inputs,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-policy-context",
                "may collect conversation data the tool doesn't need",
                "The input looks like prior conversation turns or history; it "
                "could be messages to publish, sample dialogue, version history "
                "or a transcript supplied for processing.",
            ),
            _e(
                "openai",
                "warning",
                "openai-prior-turns",
                "may request prior conversation turns",
                "An array of {role, content} objects matches a prior-turn array; "
                "it could be messages to publish, sample dialogue or a transcript "
                "supplied for processing.",
                when=("prior-turns",),
            ),
            _e(
                "openai",
                "warning",
                "openai-conversation-history",
                "may request conversation history",
                "The input name suggests conversation history or broad context; "
                "it may hold something task-specific.",
                when=("named",),
            ),
        ),
        fixes=(
            (
                "prior-turns",
                "If this collects earlier turns of the chat, remove it and take the "
                "specific input the tool needs; if it is content to process or "
                "send, say so in its description.",
            ),
            (
                "named",
                "If this collects the conversation, remove it; otherwise rename it "
                "for what it holds.",
            ),
        ),
    ),
    Rule(
        "directory-restricted-data-input",
        "input may ask for restricted data",
        "directory",
        (),
        "Don't ask for this data as a tool input.",
        _check_restricted_inputs,
        directory_effects=(
            _e(
                "openai",
                "warning",
                "openai-restricted-credentials",
                "may ask for a credential",
                "The input name suggests a password, key, token or one-time code. "
                "It may be something else, such as an opaque handle named "
                "accessToken or a code that confirms an order.",
                when=("credential",),
            ),
            _e(
                "muse",
                "warning",
                "muse-secrets",
                "may expose a credential in prompts",
                "A secret passed as a tool input goes through the prompt, which "
                "Muse forbids; the input name may not mean a secret.",
                when=("credential",),
            ),
            _e(
                "openai",
                "warning",
                "openai-restricted-payment-card",
                "may ask for payment card data",
                "The input name suggests a card number or security code; it may "
                "be something else, such as a gift card number.",
                when=("payment-card",),
            ),
            _e(
                "muse",
                "warning",
                "muse-secrets",
                "may expose payment data in prompts",
                "Card data passed as a tool input goes through the prompt, which "
                "Muse forbids; the input name may not mean card data, as with a "
                "gift card number.",
                when=("payment-card",),
            ),
            _e(
                "openai",
                "warning",
                "openai-restricted-government-id",
                "may ask for a government identifier",
                "The input name suggests a government ID; such names rarely mean "
                "anything else, but the input may take only a masked value.",
                when=("government-id",),
            ),
            _e(
                "openai",
                "warning",
                "openai-precise-location",
                "may ask for the user's precise location",
                "Coordinates are requested; OpenAI's rule is about the user's own "
                "location, and these may be any place, such as a map point.",
                when=("coordinates",),
            ),
            _e(
                "openai",
                "warning",
                "openai-precise-location",
                "may ask for the user's address",
                "An address is requested; it may not be the user's own, and "
                "shipping and billing addresses serve physical-goods checkout, "
                "which OpenAI allows elsewhere.",
                when=("address",),
            ),
        ),
        fixes=(
            (
                "credential",
                "Authenticate with OAuth or server-side configuration instead of "
                "asking for the secret; if the input isn't a secret, rename it.",
            ),
            (
                "payment-card",
                "Take payments through a secure checkout flow, not a tool input.",
            ),
            ("government-id", "Remove the government ID input."),
            (
                "coordinates",
                "If these locate the user, rely on coarse location instead; if they "
                "are a place the user chose, ignore this.",
            ),
            (
                "address",
                "If this is the user's own address and not needed for a checkout, "
                "remove it.",
            ),
        ),
    ),
    Rule(
        "directory-unsupported-use-case",
        "tool may be an unsupported use case",
        "directory",
        (),
        "Check this use case against the directory before submitting.",
        _check_use_cases,
        directory_effects=(
            _e(
                "claude",
                "warning",
                "claude-policy-money",
                "may move money or execute financial transactions",
                "The tool text describes moving money or placing trades; the "
                "phrase may describe something else, and Claude can permit these "
                "in writing. Tools marked read-only are not checked.",
                when=("money-movement", "trading"),
            ),
            _e(
                "openai",
                "warning",
                "openai-financial-services",
                "may execute money transfers or trades",
                "The tool text describes moving money or placing trades; the "
                "phrase may describe something else. Tools marked read-only are "
                "not checked.",
                when=("money-movement", "trading"),
            ),
            _e(
                "muse",
                "warning",
                "muse-finance",
                "may be a money movement or trade order tool",
                "Muse is not currently approving these; the phrase may describe "
                "something else. Tools marked read-only are not checked.",
                when=("money-movement", "trading"),
            ),
            _e(
                "claude",
                "warning",
                "claude-policy-ai-media",
                "may generate images, video or audio with AI",
                "The tool text describes generating media and mentions a model or "
                "AI; design aids such as diagrams are allowed and are not counted.",
                when=("ai-media",),
            ),
        ),
        fixes=(
            (
                "money-movement",
                "If this tool moves money or crypto, it can't be listed in these "
                "directories; if it doesn't, reword the description.",
            ),
            (
                "trading",
                "If this tool places trades, it can't be listed in these "
                "directories; if it doesn't, reword the description.",
            ),
            (
                "ai-media",
                "If this tool generates media with AI as its main service, Claude "
                "won't list it; design aids such as diagrams are allowed.",
            ),
        ),
    ),
)
