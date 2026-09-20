"""Install merge drivers for any Git working tree. Re-run after policy edits."""
from __future__ import annotations

import argparse
import pathlib
import shlex
import sys

from merge_policy import (CUSTOM_STRATEGIES, TOOL_ROOT, PolicyError, add_repository_arguments,
                          git, repository_context, rule_patterns, run_cli)

GITATTRIBUTES_MARKER_START = "# >>> merge-policy.yaml (auto-generated, do not edit by hand) >>>"
GITATTRIBUTES_MARKER_END = "# <<< merge-policy.yaml <<<"


def strategy_attribute(strategy):
    # Unspecified retains Git's binary detection and any merge.default config.
    return "!merge" if strategy == "standard" else f"merge={strategy}"


def build_gitattributes_block(policy: dict) -> str:
    lines = [GITATTRIBUTES_MARKER_START,
             f"* {strategy_attribute(policy['default_strategy'])} git-for-agents-category={policy['default_category']}"]
    for rule in policy["rules"]:
        attrs = []
        if "strategy" in rule:
            attrs.append(strategy_attribute(rule["strategy"]))
        if "category" in rule:
            attrs.append(f"git-for-agents-category={rule['category']}")
        for pattern in rule_patterns(rule):
            lines.append(f"{pattern} {' '.join(attrs)}")
    lines.append(GITATTRIBUTES_MARKER_END)
    return "\n".join(lines) + "\n"


def updated_gitattributes(existing: str, policy: dict) -> str:
    block = build_gitattributes_block(policy)
    starts = existing.count(GITATTRIBUTES_MARKER_START)
    ends = existing.count(GITATTRIBUTES_MARKER_END)
    if starts != ends or starts > 1:
        raise PolicyError("Malformed or duplicate generated .gitattributes markers; repair before setup")
    if starts:
        start = existing.index(GITATTRIBUTES_MARKER_START)
        end = existing.index(GITATTRIBUTES_MARKER_END) + len(GITATTRIBUTES_MARKER_END)
        if end < start:
            raise PolicyError("Reversed .gitattributes markers")
        return existing[:start] + block.rstrip("\n") + existing[end:]
    # Existing project attributes (e.g. Git LFS drivers) remain later and win.
    return block + existing


def configure_git_drivers(repo: pathlib.Path, policy_path: pathlib.Path, policy: dict):
    strategies = {policy["default_strategy"]} | {r["strategy"] for r in policy["rules"] if "strategy" in r}
    for strategy in sorted(strategies & CUSTOM_STRATEGIES):
        # Git executes driver commands in its POSIX shell, including on Windows.
        argv = [pathlib.Path(sys.executable).as_posix(), (TOOL_ROOT / "scripts/merge_driver.py").as_posix(),
                "--policy", policy_path.as_posix(), "--strategy", strategy]
        command = shlex.join(argv) + ' -- "%O" "%A" "%B" %P'
        for key, value in {"name": f"merge-policy: {strategy}", "driver": command, "recursive": "binary"}.items():
            git(repo, "config", "--local", f"merge.{strategy}.{key}", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_repository_arguments(parser)
    parser.add_argument("--check", action="store_true", help="Validate and print generated attributes without changing files/config")
    args = parser.parse_args()
    repo, policy_path, policy = repository_context(args)
    path = repo / ".gitattributes"
    if path.is_symlink():
        raise PolicyError("Refusing to write a symlinked .gitattributes")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = updated_gitattributes(existing, policy)
    if args.check:
        print(updated, end="")
        return 0
    configure_git_drivers(repo, policy_path, policy)
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Updated {path} and local merge drivers. Review and commit the attributes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
