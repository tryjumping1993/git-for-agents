"""Summarize candidate branch changes by review category (not validation evidence).

Generate before a sequential merge commit, with --into pointing at its original
target. For a multi-branch summary, retain the original target commit explicitly.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

from merge_policy import (add_repository_arguments, git, merge_base, repository_context, repository_ignore_case,
                          resolve_path, resolve_ref, run_cli)


def collect_stats(repo, branches, into_ref):
    per_file = defaultdict(list)
    into_id = resolve_ref(repo, into_ref)
    for branch in dict.fromkeys(branches):
        branch_id = resolve_ref(repo, branch)
        base = merge_base(repo, into_id, branch_id)
        numstat = git(repo, "diff", "--no-ext-diff", "--no-renames", "--numstat", "-z", base, branch_id, "--")
        for record in filter(None, numstat.split("\0")):
            added, removed, path = record.split("\t", 2)
            per_file[path].append((branch, None if added == "-" else int(added), None if removed == "-" else int(removed)))
    return per_file


def build_message(policy, branches, per_file, *, ignore_case=False):
    cfg = policy.get("commit_message", {})
    title = cfg.get("title_template", "Merge branches: {branches}").format(branches=", ".join(dict.fromkeys(branches)))
    lines = [title, "", "Candidate changes since divergence (review the resolved merge separately):"]
    groups = defaultdict(list)
    for path in sorted(per_file):
        category = resolve_path(policy, path, ignore_case=ignore_case)["category"] if cfg.get("group_by", "category") == "category" else "file"
        groups[category].append(path)
    if not groups:
        lines.append("No candidate file changes.")
    order = list(policy["categories"]) if cfg.get("group_by", "category") == "category" else ["file"]
    for category in order:
        if category not in groups:
            continue
        lines += ["", f"{category}:"]
        for path in groups[category]:
            parts = []
            for branch, added, removed in per_file[path]:
                stats = " (binary)" if added is None else f" (+{added}/-{removed})"
                parts.append(branch + (stats if cfg.get("include_stat_counts", True) else ""))
            resolved = resolve_path(policy, path, ignore_case=ignore_case)
            suffix = f" [policy strategy={resolved['strategy']}; category={resolved['category']}]" if cfg.get("include_strategy_used", True) else ""
            lines.append(f"- {json.dumps(path, ensure_ascii=False)}: {', '.join(parts)}{suffix}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_repository_arguments(parser)
    parser.add_argument("branches", nargs="+", help="Branches being merged")
    parser.add_argument("--into", default="HEAD", help="Original target commit/ref (default HEAD)")
    parser.add_argument("--output", type=pathlib.Path, help="Write UTF-8 directly, avoiding shell redirection encoding differences")
    args = parser.parse_args()
    repo, _, policy = repository_context(args)
    message = build_message(policy, args.branches, collect_stats(repo, args.branches, args.into),
                            ignore_case=repository_ignore_case(repo))
    if args.output:
        args.output.write_text(message, encoding="utf-8", newline="\n")
    else:
        print(message, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
