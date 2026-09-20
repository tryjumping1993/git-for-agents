"""Generic git merge driver dispatched via .gitattributes `merge=<strategy>`.

Registered by scripts/setup_merge_drivers.py as:

    merge.<strategy>.driver = python scripts/merge_driver.py --strategy <strategy> %O %A %B %P

Git calls this once per conflicted file during any merge (2-way, octopus,
rebase, cherry-pick, ...). Contract:
  - %O = temp path to the common-ancestor ("base") version
  - %A = temp path to "our" version; MUST be overwritten with the final
         merged content; this is also what git uses if we report success
  - %B = temp path to "their" version
  - %P = the real path of the file in the repo (for reporting only)

Exit code 0  -> merge is clean, %A holds the final content
Exit code !=0 -> git records the file as conflicted (stage 1/2/3 preserved),
                 %A's content is still shown to the user as the working-tree
                 version, so we still write our best-effort/annotated result.
"""
from __future__ import annotations

import argparse
import fnmatch
import pathlib
import subprocess
import sys

import yaml

from merge_policy import load_policy

MISSING = object()


def find_rule(policy: dict, real_path: str) -> dict | None:
    name = pathlib.PurePosixPath(real_path).name
    for rule in policy["rules"]:
        if fnmatch.fnmatch(real_path, rule["pattern"]) or fnmatch.fnmatch(name, rule["pattern"]):
            return rule
    return None


def merge_value(base, ours, theirs, path, conflicts):
    if ours == theirs:
        return ours
    if base == ours:
        return theirs
    if base == theirs:
        return ours

    ours_is_map = isinstance(ours, dict) or ours is MISSING
    theirs_is_map = isinstance(theirs, dict) or theirs is MISSING
    base_is_map = isinstance(base, dict) or base is MISSING
    if ours_is_map and theirs_is_map and base_is_map and not (ours is MISSING and theirs is MISSING):
        base_d = base if isinstance(base, dict) else {}
        ours_d = ours if isinstance(ours, dict) else {}
        theirs_d = theirs if isinstance(theirs, dict) else {}
        keys = dict.fromkeys(list(base_d) + list(ours_d) + list(theirs_d))
        result = {}
        for key in keys:
            sub = merge_value(
                base_d.get(key, MISSING),
                ours_d.get(key, MISSING),
                theirs_d.get(key, MISSING),
                f"{path}.{key}",
                conflicts,
            )
            if sub is not MISSING:
                result[key] = sub
        return result

    # Scalar/list/type mismatch that genuinely differs on both sides.
    conflicts.append(path)
    return ours


def three_way_merge_structured(base_text: str, ours_text: str, theirs_text: str, loader, dumper):
    base = loader(base_text) or {}
    ours = loader(ours_text) or {}
    theirs = loader(theirs_text) or {}
    conflicts: list[str] = []
    merged = merge_value(base, ours, theirs, "$", conflicts)
    if merged is MISSING:
        merged = {}
    return dumper(merged), conflicts


def strategy_json(base_text, ours_text, theirs_text):
    import json

    return three_way_merge_structured(
        base_text,
        ours_text,
        theirs_text,
        loader=json.loads,
        dumper=lambda d: json.dumps(d, indent=2, sort_keys=False) + "\n",
    )


def strategy_yaml(base_text, ours_text, theirs_text):
    return three_way_merge_structured(
        base_text,
        ours_text,
        theirs_text,
        loader=yaml.safe_load,
        dumper=lambda d: yaml.safe_dump(d, sort_keys=False),
    )


def strategy_regenerate(real_path, ours_path, rule):
    command = rule.get("command", "<regenerate command not configured>")
    print(
        f"[merge-policy] '{real_path}' is a generated lockfile (strategy=regenerate).\n"
        f"  Keeping your working copy as-is. After resolving any code conflicts, run:\n"
        f"    {command}\n"
        f"  then `git add {real_path}` to finish.",
        file=sys.stderr,
    )
    return 1  # always leave as a conflict so it can't be silently committed stale


def strategy_manual_conflict(real_path):
    print(
        f"[merge-policy] '{real_path}' cannot be auto-merged (strategy=manual-conflict).\n"
        f"  Resolve by hand, e.g.:\n"
        f"    git checkout --ours -- {real_path}   # or --theirs\n"
        f"    git add {real_path}",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("base_path")
    parser.add_argument("ours_path")
    parser.add_argument("theirs_path")
    parser.add_argument("real_path")
    args = parser.parse_args()

    if args.strategy == "manual-conflict":
        return strategy_manual_conflict(args.real_path)

    if args.strategy == "regenerate":
        policy = load_policy()
        rule = find_rule(policy, args.real_path) or {}
        return strategy_regenerate(args.real_path, args.ours_path, rule)

    ours_file = pathlib.Path(args.ours_path)
    base_text = pathlib.Path(args.base_path).read_text(encoding="utf-8")
    ours_text = ours_file.read_text(encoding="utf-8")
    theirs_text = pathlib.Path(args.theirs_path).read_text(encoding="utf-8")

    if args.strategy == "json-deep-merge":
        merged_text, conflicts = strategy_json(base_text, ours_text, theirs_text)
    elif args.strategy == "yaml-deep-merge":
        merged_text, conflicts = strategy_yaml(base_text, ours_text, theirs_text)
    else:
        print(f"[merge-policy] unknown strategy '{args.strategy}', falling back to `git merge-file`", file=sys.stderr)
        return subprocess.run(["git", "merge-file", args.ours_path, args.base_path, args.theirs_path]).returncode

    ours_file.write_text(merged_text, encoding="utf-8")

    if conflicts:
        print(
            f"[merge-policy] '{args.real_path}' merged with {len(conflicts)} key-level conflict(s) "
            f"kept as 'ours' at: {', '.join(conflicts)}. Review and edit, then `git add`.",
            file=sys.stderr,
        )
        return 1

    print(f"[merge-policy] '{args.real_path}' auto-merged cleanly (strategy={args.strategy}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
