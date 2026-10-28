#!/usr/bin/env python3
"""Measure when the preparing-directory-submissions skill activates.

Runs a fixed set of prompts, several times each, in fresh Claude Code
sessions that have both M3 skills installed, and records which skill each
session loads first. Prompts about directory submission, listing, review or
rejection should load preparing-directory-submissions; general M3 and MCP
testing prompts should load testing-with-m3. A session that loads no skill,
or times out, counts as a miss.

Compare skill versions by passing git refs: each ref's skills/ directory is
installed into its own project. "WORKTREE" means the files on disk.

This makes real model calls. Run it by hand; it is not part of CI.

Usage:
  python3 benchmarks/directory-readiness/run_triggers.py [--ref REF ...] [--reps N] [--model MODEL]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_scenario import REPO, SKILLS, prepare, skills_loaded

SKILL = "preparing-directory-submissions"
OTHER = "testing-with-m3"
# (prompt, the skill that should load first)
PROMPTS: list[tuple[str, str]] = [
    ("Is this MCP server ready to list in the Claude connector directory?", SKILL),
    (
        'OpenAI rejected our ChatGPT app. The email says: "Your plugin returns '
        'user-related data types that are not disclosed in your privacy policy." '
        "What do we fix?",
        SKILL,
    ),
    (
        "We're about to submit our Codex plugin for review. Can you check it first?",
        SKILL,
    ),
    (
        "Muse's reviewers flagged that one of our tools hides a send action. Help me sort it out.",
        SKILL,
    ),
    (
        "What do I need to change before submitting this connector to Anthropic's directory?",
        SKILL,
    ),
    ("Write tests for my MCP server.", OTHER),
    ("Add an m3 test that checks search_notes returns the kickoff note.", OTHER),
    ("m3 test says my test has no suite name. How do I fix it?", OTHER),
    ("Set up M3 in this project.", OTHER),
]


def skills_source(ref: str, root: Path) -> Path:
    """A directory holding the skills/ tree of a git ref, or the worktree."""
    if ref == "WORKTREE":
        return REPO
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref, "skills"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    target = root / "skills-source"
    target.mkdir()
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    return target


def run_once(project: Path, env: dict[str, str], prompt: str, model: str) -> str:
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as out:
        try:
            subprocess.run(
                [
                    "claude", "-p", prompt, "--model", model,
                    "--output-format", "stream-json", "--verbose",
                    "--max-turns", "2", "--allowedTools", "Skill,Read,Glob,Grep",
                    "--no-session-persistence",
                ],
                cwd=project, env=env, stdout=out, stderr=subprocess.STDOUT,
                timeout=600, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            # One hung session is a miss, not the end of the matrix.
            return "timeout"
        loaded = skills_loaded(Path(out.name))
    return loaded[0] if loaded else "none"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / ".runs")
    args = parser.parse_args()
    refs = args.ref or ["WORKTREE"]

    projects = {}
    for ref in refs:
        root = Path(tempfile.mkdtemp(prefix="readiness-triggers-"))
        projects[ref] = prepare(
            "clean-server", "current", root, SKILLS, skills_source(ref, root)
        )

    jobs = [
        (ref, index, prompt)
        for ref in refs
        for index, (prompt, _) in enumerate(PROMPTS)
        for _ in range(args.reps)
    ]
    results: dict[tuple[str, int], list[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = {
            pool.submit(run_once, *projects[ref], prompt, args.model): (ref, index)
            for ref, index, prompt in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            results.setdefault(futures[future], []).append(future.result())

    rows = []
    print("| Prompt | Expect | " + " | ".join(refs) + " |")
    print("|---|---|" + "---|" * len(refs))
    for index, (prompt, expected) in enumerate(PROMPTS):
        cells = []
        for ref in refs:
            loaded = results.get((ref, index), [])
            hits = sum(skill == expected for skill in loaded)
            cells.append(f"{hits}/{len(loaded)} ({', '.join(sorted(set(loaded)))})")
        rows.append(
            {
                "prompt": prompt,
                "expected": expected,
                "results": {ref: results.get((ref, index), []) for ref in refs},
            }
        )
        print(f"| {prompt[:70]} | {expected} | " + " | ".join(cells) + " |")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "triggers.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
