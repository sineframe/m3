#!/bin/sh
# Public bootstrap for stable M3 releases, with an alpha fallback before the
# first final release. Supported version tags are vX.Y.Z, optionally followed
# by aN, bN, or rcN; exact tag selection uses the same grammar. Canary builds
# are opt-in prereleases tagged canary-main or canary-pr-N.
set -eu
REPOSITORY='sineframe/m3'
command -v curl >/dev/null 2>&1 || { echo 'curl is required' >&2; exit 1; }
if command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON=$(command -v python3)
elif command -v uv >/dev/null 2>&1; then
    uv python install 3.13 >/dev/null || { echo 'uv could not install Python 3.13' >&2; exit 1; }
    BOOTSTRAP_PYTHON=$(uv python find 3.13) || { echo 'uv could not locate Python 3.13' >&2; exit 1; }
else
    echo 'Python 3 or uv is required' >&2
    exit 1
fi
mode=stable
exact_tag=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prerelease) mode=prerelease ;;
        --tag) [ "$#" -ge 2 ] || { echo '--tag requires a tag' >&2; exit 2; }; exact_tag=$2; shift ;;
        --canary) mode=canary; exact_tag=canary-main ;;
        --pr)
            [ "$#" -ge 2 ] || { echo '--pr requires a pull request number' >&2; exit 2; }
            case "$2" in ''|0*|*[!0-9]*) echo '--pr requires a pull request number' >&2; exit 2 ;; esac
            mode=canary; exact_tag="canary-pr-$2"; shift ;;
        -h|--help) echo 'Usage: install-latest.sh [--prerelease | --tag vX.Y.Z[aN|bN|rcN] | --canary | --pr N]'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/m3-bootstrap.XXXXXX") || exit 1
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
page=1
while :; do
    page_file="$TMP_DIR/releases-$page.json"
    curl --fail --silent --show-error --location \
        "https://api.github.com/repos/${REPOSITORY}/releases?per_page=100&page=${page}" \
        --output "$page_file"
    count=$("$BOOTSTRAP_PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$page_file")
    [ "$count" -eq 100 ] || break
    page=$((page + 1))
done
tag=$("$BOOTSTRAP_PYTHON" - "$TMP_DIR" "$mode" "$exact_tag" <<'PY'
import json, pathlib, re, sys
root, mode, exact = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
pattern = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
canary = re.compile(r"^canary-(?:main|pr-[1-9][0-9]*)$")
rows = []
for path in root.glob("releases-*.json"):
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise SystemExit("GitHub returned an invalid release list")
    rows.extend(data)
def parse(tag):
    match = pattern.fullmatch(tag)
    if not match:
        return None
    major, minor, patch, phase, serial = match.groups()
    rank = {None: 3, "a": 0, "b": 1, "rc": 2}[phase]
    return (int(major), int(minor), int(patch), rank, int(serial or 0))
rows = [r for r in rows if isinstance(r, dict) and not r.get("draft")]
if mode == "canary":
    match = next((r for r in rows if r.get("tag_name") == exact and canary.fullmatch(exact) and r.get("prerelease")), None)
    if match is None:
        raise SystemExit(f"no published canary build {exact}; pull requests need the canary label")
    if not any(a.get("name") == "install.sh" for a in match.get("assets", [])):
        raise SystemExit("requested canary has no install.sh asset")
    print(exact)
    raise SystemExit
rows = [r for r in rows if parse(r.get("tag_name", ""))]
if exact:
    match = next((r for r in rows if r["tag_name"] == exact), None)
    if match is None:
        raise SystemExit("requested tag is invalid or not a published release")
    if not any(a.get("name") == "install.sh" for a in match.get("assets", [])):
        raise SystemExit("requested release has no install.sh asset")
    print(match["tag_name"])
    raise SystemExit
if mode == "prerelease":
    choices = [r for r in rows if r.get("prerelease")]
elif mode == "stable":
    choices = [r for r in rows if not r.get("prerelease")]
    if not choices:
        choices = [r for r in rows if r.get("prerelease")]
else:
    raise SystemExit("invalid release selection")
choices = [r for r in choices if any(a.get("name") == "install.sh" for a in r.get("assets", []))]
if not choices:
    raise SystemExit("no published M3 release with install.sh was found")
print(max(choices, key=lambda r: parse(r["tag_name"]))["tag_name"])
PY
)
base_url="https://github.com/${REPOSITORY}/releases/download/${tag}"
curl --fail --silent --show-error --location "${base_url}/manifest.json" --output "$TMP_DIR/manifest.json"
curl --fail --silent --show-error --location "${base_url}/install.sh" --output "$TMP_DIR/install.sh"
curl --fail --silent --show-error --location "${base_url}/SHA256SUMS" --output "$TMP_DIR/SHA256SUMS"
"$BOOTSTRAP_PYTHON" - "$TMP_DIR/manifest.json" "$tag" "$TMP_DIR/install.sh" "$TMP_DIR/SHA256SUMS" <<'PY'
import hashlib, json, pathlib, re, sys
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text())
tag=sys.argv[2]
if tag.startswith("canary-"):
    version=manifest.get("version")
    if manifest.get("release_tag") != tag or not isinstance(version,str) or not re.fullmatch(r"[0-9A-Za-z.]+\.dev[0-9]+",version): raise SystemExit("canary manifest does not match the requested build")
elif manifest.get("version") != tag[1:]: raise SystemExit("release manifest version mismatch")
for name, path in (("install.sh", pathlib.Path(sys.argv[3])), ("SHA256SUMS", pathlib.Path(sys.argv[4]))):
    expected=manifest.get("assets",{}).get(name)
    if not isinstance(expected,str) or not re.fullmatch(r"[0-9a-f]{64}",expected): raise SystemExit(f"release manifest has no valid {name} hash")
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected: raise SystemExit(f"{name} checksum mismatch")
PY
sh "$TMP_DIR/install.sh"
