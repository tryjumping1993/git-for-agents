"""Read-only branch overlap and review plan for any Git working tree."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from merge_policy import (add_repository_arguments, git, merge_base, repository_context, repository_ignore_case,
                          resolve_path, resolve_ref, run_cli)

MANUAL_STRATEGIES = {"regenerate", "manual-conflict"}


def changed_files(repo, base, ref):
    # Disable rename presentation: both old and new paths require review.
    return set(filter(None, git(repo, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base, ref, "--").split("\0")))


def analyze(repo, policy, target, branches):
    branches = list(dict.fromkeys(branches))
    target_id = resolve_ref(repo, target)
    ignore_case = repository_ignore_case(repo)
    by_file, target_overlaps = defaultdict(list), defaultdict(list)
    for branch in branches:
        branch_id = resolve_ref(repo, branch)
        base = merge_base(repo, target_id, branch_id)
        touched = changed_files(repo, base, branch_id)
        for path in sorted(touched):
            by_file[path].append(branch)
        for path in sorted(touched & changed_files(repo, base, target_id)):
            target_overlaps[path].append(branch)
    files = [{"path": path, **resolve_path(policy, path, ignore_case=ignore_case), "branches": bs,
              "target_overlap": target_overlaps[path], "shared": len(bs) > 1}
             for path, bs in sorted(by_file.items())]
    def score(branch):
        risky = [f for f in files if branch in f["branches"] and (f["shared"] or branch in f["target_overlap"])]
        return (sum(f["strategy"] in MANUAL_STRATEGIES for f in risky),
                sum(branch in f["target_overlap"] for f in risky), len(risky))
    categories = {f["category"] for f in files}
    return {"target": target, "target_commit": target_id, "branches": branches,
            "files": files, "suggested_order": sorted(branches, key=score),
            "review": {name: cfg for name, cfg in policy["categories"].items() if name in categories}}


def format_report(report):
    lines = [f"Analyzing {len(report['branches'])} branch(es) against {report['target']!r}", ""]
    if not report["files"]:
        lines.append("No candidate file changes since divergence.")
    elif not any(f["shared"] or f["target_overlap"] for f in report["files"]):
        lines.append("No file overlap detected. Cross-file dependencies and behavior still require review.")
    for category, cfg in report["review"].items():
        lines += ["", f"{category}: {cfg.get('description', '')}"]
        for item in report["files"]:
            if item["category"] != category:
                continue
            tags = [f"strategy={item['strategy']}"]
            if item["shared"]:
                tags.append("shared across candidate branches")
            if item["target_overlap"]:
                tags.append("target also changed since divergence from " + ", ".join(item["target_overlap"]))
            if item["strategy"] in MANUAL_STRATEGIES:
                tags.append("manual resolution if file driver is invoked")
            lines.append(f"  - {json.dumps(item['path'], ensure_ascii=False)}: {', '.join(item['branches'])} [{'; '.join(tags)}]")
        lines.extend(f"  Review: {check}" for check in cfg.get("checks", []))
    lines += ["", "Suggested order (file-overlap heuristic, not a dependency or safety guarantee):"]
    lines.extend(f"  {branch}" for branch in report["suggested_order"])
    lines += ["", "Merge one branch at a time and re-run analysis as the target advances.",
              "This reports configured policy, not actual driver execution or completed validation.",
              "Nested/local attributes and merge.default may override policy. A clean merge does not approve a change."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_repository_arguments(parser)
    parser.add_argument("target", help="Target branch/ref")
    parser.add_argument("branches", nargs="+", help="Candidate branches/refs")
    parser.add_argument("--json", action="store_true", help="Output a machine-readable review plan")
    args = parser.parse_args()
    repo, _, policy = repository_context(args)
    report = analyze(repo, policy, args.target, args.branches)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else format_report(report), end="\n" if args.json else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
