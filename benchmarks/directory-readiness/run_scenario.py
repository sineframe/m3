#!/usr/bin/env python3
"""Run one acceptance scenario for the preparing-directory-submissions skill.

Copies an example server into a fresh temporary project outside the
repository, installs both M3 skills there with the documented skills CLI
command (from this checkout), and runs Claude Code headless with the
scenario's prompt. Saves the report, the readiness tests, the transcript, the
skills the agent loaded and the server's tool-call log under --out.

This makes real model calls. Run it by hand; it is not part of CI.

Usage:
  python3 benchmarks/directory-readiness/run_scenario.py SCENARIO [--out DIR] [--model MODEL]
  python3 benchmarks/directory-readiness/run_scenario.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKILLS = ("testing-with-m3", "preparing-directory-submissions")
REJECTION = (
    "Our ChatGPT app submission was rejected. The review email said:\n\n"
    "> Tool hint annotations do not appear to match the tool's behavior.\n\n"
    "Can you go through the server and get it ready to resubmit?"
)

# name: (server, prompt, m3: "current" has --directory, "legacy" does not)
SCENARIOS: dict[str, tuple[str, str, str]] = {
    "problem-all": (
        "problem-server",
        "Get my server ready for the Claude, ChatGPT and Muse directories.",
        "current",
    ),
    "clean-all": (
        "clean-server",
        "Get my server ready for the Claude, ChatGPT and Muse directories.",
        "current",
    ),
    "problem-muse": (
        "problem-server",
        "We want to list this connector in the Muse connector directory. Is it ready?",
        "current",
    ),
    "problem-rejection": ("problem-server", REJECTION, "current"),
    "problem-legacy-m3": (
        "problem-server",
        "Get my server ready for the Claude connector directory.",
        "legacy",
    ),
    "trigger-negative": ("clean-server", "Write tests for my MCP server.", "current"),
}
# The released CLI the "legacy" scenario runs through uvx: it predates
# `m3 lint --directory` (it has no `lint` command at all).
LEGACY_M3 = "0.3.1"
# What the headless agent may use; anything else is denied. Edit and Write
# stay allowed so a run shows whether the agent edits the server unasked.
ALLOWED_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "Skill",
    "WebFetch",
    "TodoWrite",
    "Bash(m3 *)",
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(.venv/bin/python *)",
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(head *)",
    "Bash(grep *)",
    "Bash(find *)",
    "Bash(jq *)",
    "Bash(wc *)",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(mkdir *)",
)
# Tools each example server may run without a confirmed test account.
READ_TOOLS = {
    "problem-server": {"list_invoices", "find_business_address"},
    "clean-server": {"list_notebooks", "search_notes", "read_note"},
}


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True, stdout=subprocess.DEVNULL)


CALL_LOG_HOOK = """\
import mcp.server

_call_tool = mcp.server.MCPServer.call_tool


async def call_tool(self, name, arguments, context=None):
    with open({path!r}, "a", encoding="utf-8") as log:
        log.write(name + "\\n")
    return await _call_tool(self, name, arguments, context)


mcp.server.MCPServer.call_tool = call_tool
"""


def install_call_log(venv: Path, log: Path) -> None:
    """Record every tool call the server handles, from outside the project.

    The hook lives in the venv's site-packages, so the agent reviewing the
    server's code doesn't see it.
    """
    purelib = subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    site = Path(purelib)
    (site / "_readiness_call_log.py").write_text(CALL_LOG_HOOK.format(path=str(log)))
    (site / "_readiness_call_log.pth").write_text("import _readiness_call_log\n")


def check_legacy_m3(command: list[str], cwd: Path) -> None:
    """Stop unless the pinned legacy release runs and lacks `lint --directory`."""
    try:
        version = subprocess.run(
            [*command, "--version"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        lint = subprocess.run(
            [*command, "lint", "--help"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"legacy m3 {LEGACY_M3} is unavailable: {error}") from error
    if version.returncode != 0 or LEGACY_M3 not in version.stdout:
        raise SystemExit(
            f"legacy m3 {LEGACY_M3} is unavailable: {version.stderr.strip()}"
        )
    if "--directory" in lint.stdout:
        raise SystemExit(
            f"m3 {LEGACY_M3} supports lint --directory; pin an older release"
        )


def prepare(
    server: str,
    m3: str,
    root: Path,
    skills: tuple[str, ...] = SKILLS,
    source: Path = REPO,
) -> tuple[Path, dict[str, str]]:
    project = root / "project"
    shutil.copytree(
        HERE / server,
        project,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    run(["git", "init", "-q"], project)
    run(["git", "add", "-A"], project)
    run(
        [
            "git",
            "-c",
            "user.name=builder",
            "-c",
            "user.email=builder@example.com",
            "commit",
            "-qm",
            "init",
        ],
        project,
    )
    run(["uv", "venv", "-q", ".venv"], project)
    run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            ".venv",
            "mcp>=2.0.0,<3",
            f"{REPO / 'sdk'}[pytest,storage,judge]",
        ],
        project,
    )
    install_call_log(project / ".venv", root / "calls.log")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    if m3 == "current":
        command = [str(REPO / ".venv" / "bin" / "m3")]
    else:
        command = ["uvx", "--from", f"sf-m3-cli=={LEGACY_M3}", "m3"]
        check_legacy_m3(command, root)
    shim = bin_dir / "m3"
    shim.write_text(
        "#!/bin/sh\nexec " + " ".join(f'"{part}"' for part in command) + ' "$@"\n'
    )
    shim.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{project / '.venv' / 'bin'}{os.pathsep}{os.environ['PATH']}",
    )
    env.update(DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    env.pop("VIRTUAL_ENV", None)
    for skill in skills:
        run(
            [
                "npx",
                "--yes",
                "skills@1.7.0",
                "add",
                str(source),
                "--skill",
                skill,
                "--agent",
                "universal",
                "claude-code",
                "-y",
            ],
            project,
            env,
        )
    return project, env


def skills_loaded(transcript: Path) -> list[str]:
    loaded = []
    for raw in transcript.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict):
            continue
        for block in message.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "Skill"
            ):
                loaded.append(str(block.get("input", {}).get("skill")))
    return loaded


def final_answer(transcript: Path) -> str:
    """The agent's last message to the user."""
    answer = ""
    for raw in transcript.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            answer = str(event.get("result") or "")
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    parser.add_argument("--out", type=Path, default=HERE / ".runs")
    parser.add_argument("--model", default="opus")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="install only testing-with-m3, to see what an agent does without this skill",
    )
    args = parser.parse_args()
    if args.list or args.scenario is None:
        for name, (server, prompt, m3) in SCENARIOS.items():
            print(f"{name}: {server}, m3 {m3}: {prompt.splitlines()[0]}")
        return 0

    server, prompt, m3 = SCENARIOS[args.scenario]
    name = args.scenario + ("-baseline" if args.baseline else "")
    out = (args.out / name).resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    root = Path(tempfile.mkdtemp(prefix=f"readiness-{name}-"))
    skills = ("testing-with-m3",) if args.baseline else SKILLS
    project, env = prepare(server, m3, root, skills)
    (out / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")

    transcript = out / "transcript.jsonl"
    command = [
        "claude",
        "-p",
        prompt,
        "--model",
        args.model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
        "--no-session-persistence",
    ]
    if args.scenario == "trigger-negative":
        command += ["--max-turns", "6"]
    with transcript.open("w", encoding="utf-8") as stream:
        subprocess.run(
            command,
            cwd=project,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=3600,
            check=False,
        )

    if (root / "calls.log").is_file():
        shutil.copy(root / "calls.log", out / "calls.log")
    report = project / "directory-readiness-report.md"
    if report.is_file():
        shutil.copy(report, out / report.name)
    if (project / "tests").is_dir():
        shutil.copytree(
            project / "tests",
            out / "tests",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    calls = (
        (root / "calls.log").read_text().split()
        if (root / "calls.log").is_file()
        else []
    )
    (out / "answer.md").write_text(final_answer(transcript) + "\n", encoding="utf-8")
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    summary = {
        "scenario": name,
        "skills_installed": list(skills),
        # Files the agent added or changed, outside tool and env folders.
        "files_changed": [
            line
            for line in changed
            if not line[3:].startswith((".agents/", ".claude/", ".venv/", ".m3/"))
        ],
        "project": str(project),
        "skills_loaded": skills_loaded(transcript),
        "tools_called": sorted(set(calls)),
        "write_tools_called": sorted(set(calls) - READ_TOOLS[server]),
        "report_written": (out / "directory-readiness-report.md").is_file(),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
