"""Git file merge driver: write %A only on a clean structured merge.

Custom drivers run when Git needs a file-level merge; they are not review
gates for one-sided changes or other merges Git can resolve without a driver.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

from merge_policy import CUSTOM_STRATEGIES, UniqueKeyLoader, load_policy, resolve_path, repository_ignore_case

MISSING = object()


def same(left, right):
    """Python considers True == 1; structured data must preserve scalar types."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def merge_value(base, ours, theirs, path, conflicts):
    if same(ours, theirs):
        return ours
    if same(base, ours):
        return theirs
    if same(base, theirs):
        return ours
    # Deleting a mapping while the other branch modifies it is a conflict at
    # the mapping itself, including changes that add keys to an empty map.
    if ours is MISSING or theirs is MISSING:
        conflicts.append(path)
        return ours
    if isinstance(ours, dict) and isinstance(theirs, dict) and (isinstance(base, dict) or base is MISSING):
        base_d = {} if base is MISSING else base
        result = {}
        for key in dict.fromkeys(list(base_d) + list(ours) + list(theirs)):
            value = merge_value(base_d.get(key, MISSING), ours.get(key, MISSING),
                                theirs.get(key, MISSING), f"{path}[{json.dumps(key)}]", conflicts)
            if value is not MISSING:
                result[key] = value
        return result
    # Lists are atomic: append order can change program or deployment behavior.
    conflicts.append(path)
    return ours


def validate_tree(value, ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise ValueError("Cyclic YAML aliases are not supported")
        ancestors.add(id(value))
        if isinstance(value, dict) and any(not isinstance(k, str) for k in value):
            raise ValueError("Mapping keys must be strings")
        for child in value.values() if isinstance(value, dict) else value:
            validate_tree(child, ancestors)
        ancestors.remove(id(value))
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError("Only JSON-compatible YAML data is supported (no dates, sets, or custom tags)")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not supported")


def three_way_merge_structured(base_text, ours_text, theirs_text, loader, dumper):
    base = MISSING if base_text == "" else loader(base_text)
    ours, theirs = loader(ours_text), loader(theirs_text)
    for value in (base, ours, theirs):
        if value is not MISSING:
            validate_tree(value)
    conflicts = []
    merged = merge_value(base, ours, theirs, "$", conflicts)
    # Preserve ours byte-for-byte at the CLI on conflict, not a partial merge.
    return (ours_text if conflicts else dumper(merged)), conflicts


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def strategy_json(base_text, ours_text, theirs_text):
    return three_way_merge_structured(base_text, ours_text, theirs_text,
        loader=lambda text: json.loads(text, object_pairs_hook=unique_json),
        dumper=lambda value: json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def strategy_yaml(base_text, ours_text, theirs_text):
    return three_way_merge_structured(base_text, ours_text, theirs_text,
        loader=lambda text: yaml.load(text, Loader=UniqueKeyLoader),
        dumper=lambda value: yaml.safe_dump(value, sort_keys=False, allow_unicode=True))


def write_result(path, text):
    """Complete the write before replacing ours, including on I/O failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".merge-policy-", delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, choices=sorted(CUSTOM_STRATEGIES))
    parser.add_argument("--policy", type=pathlib.Path, default=pathlib.Path("merge-policy.yaml"))
    parser.add_argument("base_path", type=pathlib.Path)
    parser.add_argument("ours_path", type=pathlib.Path)
    parser.add_argument("theirs_path", type=pathlib.Path)
    parser.add_argument("real_path")
    args = parser.parse_args()
    try:
        if args.strategy == "manual-conflict":
            print(f"[merge-policy] {args.real_path!r}: resolve manually; ours is unchanged.", file=sys.stderr)
            return 1
        if args.strategy == "regenerate":
            rule = resolve_path(load_policy(args.policy), args.real_path,
                                ignore_case=repository_ignore_case(pathlib.Path.cwd()))
            command = rule.get("command", "Use the repository's documented generator and pinned tool version.")
            print(f"[merge-policy] {args.real_path!r}: generated artifact requires regeneration.\n"
                  f"  {command}\n  Run from the owning package/project directory after resolving inputs; "
                  "review the result, then stage it. No command was executed.", file=sys.stderr)
            return 1
        texts = [p.read_text(encoding="utf-8") for p in (args.base_path, args.ours_path, args.theirs_path)]
        strategy = strategy_json if args.strategy == "json-deep-merge" else strategy_yaml
        merged, conflicts = strategy(*texts)
        if conflicts:
            print(f"[merge-policy] {args.real_path!r}: conflicts at {', '.join(conflicts)}; "
                  "ours is unchanged. Review Git's base/ours/theirs stages and resolve.", file=sys.stderr)
            return 1
        write_result(args.ours_path, merged)
        return 0
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError, subprocess.CalledProcessError) as exc:
        print(f"[merge-policy] {args.real_path!r}: cannot merge ({exc}); ours is unchanged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
