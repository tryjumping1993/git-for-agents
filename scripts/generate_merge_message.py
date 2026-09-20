"""Generate a human-readable merge commit message summarizing per-file,
per-branch changes, driven by the `commit_message` section of
merge-policy.yaml.

Typical usage, combining several feature branches into the current branch.
Run this BEFORE the merge is committed, with --into set to the commit HEAD
was at before merging (not a branch that the merges have already landed on):

    git merge --no-commit --no-ff feature/a feature/b feature/c
    python scripts/generate_merge_message.py feature/a feature/b feature/c --into HEAD > /tmp/msg.txt
    git commit -F /tmp/msg.txt

Produces something like:

    Merge branches: feature/a, feature/b, feature/c

    Combined changes by file:
    - src/app.py: feature/a (+12/-3), feature/b (+5/-1)
    - README.md: feature/c (+8/-0)
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from collections import defaultdict

from merge_policy import REPO_ROOT, load_policy


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout


def strategy_for(policy: dict, path: str) -> str:
    for rule in policy["rules"]:
        if fnmatch.fnmatch(path, rule["pattern"]):
            return rule["strategy"]
    return policy["default_strategy"]


def collect_stats(branches: list[str], into_ref: str) -> dict[str, list[tuple[str, int, int]]]:
    per_file: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for branch in branches:
        merge_base = git("merge-base", into_ref, branch).strip()
        numstat = git("diff", "--numstat", f"{merge_base}..{branch}")
        for line in numstat.splitlines():
            if not line.strip():
                continue
            added, removed, path = line.split("\t", 2)
            added_n = 0 if added == "-" else int(added)
            removed_n = 0 if removed == "-" else int(removed)
            per_file[path].append((branch, added_n, removed_n))
    return per_file


def build_message(policy: dict, branches: list[str], per_file: dict) -> str:
    cfg = policy.get("commit_message", {})
    title = cfg.get("title_template", "Merge branches: {branches}").format(branches=", ".join(branches))
    lines = [title, ""]
    lines.append("Combined changes by file:")
    for path in sorted(per_file):
        entries = per_file[path]
        parts = []
        for branch, added, removed in entries:
            if cfg.get("include_stat_counts", True):
                parts.append(f"{branch} (+{added}/-{removed})")
            else:
                parts.append(branch)
        suffix = ""
        if cfg.get("include_strategy_used", True):
            suffix = f"  [strategy={strategy_for(policy, path)}]"
        lines.append(f"- {path}: {', '.join(parts)}{suffix}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branches", nargs="+", help="Branches being merged in")
    parser.add_argument("--into", default="HEAD", help="Target ref the branches are merged into (default HEAD)")
    args = parser.parse_args()

    policy = load_policy()
    per_file = collect_stats(args.branches, args.into)
    sys.stdout.write(build_message(policy, args.branches, per_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
