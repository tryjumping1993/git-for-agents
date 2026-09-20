"""Pre-merge analysis: report which branches touch the same files before you
start merging them in, and suggest a merge order.

This does NOT run git merge or invoke any merge driver — it's read-only, safe
to run at any time, and meant as step 0 of the workflow:

    python scripts/analyze_merge_plan.py main feature/a feature/b feature/c

The first argument is the target branch/ref everything will be merged into;
the rest are the branches you're planning to combine.
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

# Strategies that can usually combine independent same-file changes without
# a human; everything else needs a human to look even if git reports "clean".
AUTO_COMBINABLE = {"union", "json-deep-merge", "yaml-deep-merge"}
# Strategies that are conflicts by construction, regardless of overlap.
ALWAYS_MANUAL = {"manual-conflict", "regenerate"}


def changed_files(base: str, ref: str) -> set[str]:
    out = git("diff", "--name-only", f"{base}..{ref}")
    return {line for line in out.splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Branch/ref the branches will be merged into")
    parser.add_argument("branches", nargs="+", help="Candidate branches to merge in")
    args = parser.parse_args()

    policy = load_policy()
    merge_bases = {b: git("merge-base", args.target, b).strip() for b in args.branches}
    files_by_branch = {b: changed_files(merge_bases[b], b) for b in args.branches}
    # What target itself changed since diverging from each branch — needed
    # because target (e.g. main) may already have moved on independently.
    files_by_target = {b: changed_files(merge_bases[b], args.target) for b in args.branches}

    file_to_branches: dict[str, list[str]] = defaultdict(list)
    for branch, files in files_by_branch.items():
        for f in files:
            file_to_branches[f].append(branch)

    also_on_target: dict[str, list[str]] = defaultdict(list)
    for branch, files in files_by_branch.items():
        for f in files & files_by_target[branch]:
            also_on_target[f].append(branch)

    shared = {f: bs for f, bs in file_to_branches.items() if len(bs) > 1}

    print(f"Analyzing {len(args.branches)} branch(es) against '{args.target}'\n")

    if not shared and not also_on_target:
        print("No files are touched by more than one branch, and none overlap with")
        print(f"'{args.target}''s own changes — every branch can be merged in any order.")
    else:
        if shared:
            print("Files touched by more than one branch:")
            for f in sorted(shared):
                strategy = strategy_for(policy, f)
                bs = shared[f]
                if strategy in ALWAYS_MANUAL:
                    risk = "ALWAYS CONFLICTS (strategy=" + strategy + ") — resolve by hand"
                elif strategy in AUTO_COMBINABLE:
                    risk = f"likely auto-mergeable (strategy={strategy}); verify after merge"
                else:
                    risk = f"real conflict risk if changes overlap (strategy={strategy}) — review diffs"
                print(f"  - {f}: {', '.join(bs)}  [{risk}]")
        if also_on_target:
            print(f"\nFiles ALSO changed on '{args.target}' itself since branching (higher risk):")
            for f in sorted(also_on_target):
                strategy = strategy_for(policy, f)
                print(f"  - {f}: {', '.join(also_on_target[f])} (vs '{args.target}')  [strategy={strategy}]")

    # Suggest an order: fewest shared/risky files first, so low-risk branches
    # land cleanly and reduce the diff surface before tackling risky ones.
    def risk_score(branch: str) -> tuple[int, int, int]:
        touched = files_by_branch[branch]
        manual = sum(1 for f in touched if strategy_for(policy, f) in ALWAYS_MANUAL and f in shared)
        target_overlap = sum(1 for f in touched if branch in also_on_target.get(f, []))
        overlap = sum(1 for f in touched if f in shared)
        return (manual, target_overlap, overlap)

    ordered = sorted(args.branches, key=risk_score)
    print("\nSuggested merge order (lowest conflict risk first):")
    for b in ordered:
        print(f"  {b}")
    print(
        "\nRemember: merge these ONE AT A TIME (`git merge <branch> --no-edit`), never as a\n"
        "single octopus merge — octopus bypasses custom merge drivers entirely."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
