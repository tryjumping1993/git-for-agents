"""Apply merge-policy.yaml to this repo's local git config and .gitattributes.

Run once after cloning, and again whenever merge-policy.yaml changes:

    python scripts/setup_merge_drivers.py

This only touches:
  - .gitattributes            (committed, shared with everyone)
  - .git/config (local)       (per-clone `git config merge.<name>.*` entries)

Every teammate/agent must run this once; .gitattributes alone can reference
custom drivers, but the *driver command itself* is intentionally local repo
config (per git's design) so it must be (re)applied per clone.
"""
from __future__ import annotations

import subprocess
import sys

from merge_policy import REPO_ROOT, BUILTIN_STRATEGIES, CUSTOM_STRATEGIES, load_policy

GITATTRIBUTES_MARKER_START = "# >>> merge-policy.yaml (auto-generated, do not edit by hand) >>>"
GITATTRIBUTES_MARKER_END = "# <<< merge-policy.yaml <<<"


def build_gitattributes_block(policy: dict) -> str:
    lines = [GITATTRIBUTES_MARKER_START]
    for rule in policy["rules"]:
        strategy = rule["strategy"]
        if strategy == "standard":
            continue  # git's default behavior, no attribute needed
        lines.append(f"{rule['pattern']} merge={strategy}")
        for excl in rule.get("exclude", []):
            lines.append(f"{excl} merge=text")
    lines.append(GITATTRIBUTES_MARKER_END)
    return "\n".join(lines) + "\n"


def update_gitattributes(policy: dict) -> None:
    path = REPO_ROOT / ".gitattributes"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    block = build_gitattributes_block(policy)
    if GITATTRIBUTES_MARKER_START in existing:
        start = existing.index(GITATTRIBUTES_MARKER_START)
        end = existing.index(GITATTRIBUTES_MARKER_END) + len(GITATTRIBUTES_MARKER_END)
        existing = existing[:start] + block.rstrip("\n") + existing[end:]
    else:
        sep = "\n" if existing and not existing.endswith("\n") else ""
        existing = existing + sep + ("\n" if existing else "") + block

    path.write_text(existing, encoding="utf-8")
    print(f"Updated {path}")


def configure_git_drivers(policy: dict) -> None:
    strategies = {rule["strategy"] for rule in policy["rules"]}
    for strategy in sorted(strategies & CUSTOM_STRATEGIES):
        driver_cmd = f'python "{REPO_ROOT / "scripts" / "merge_driver.py"}" --strategy {strategy} %O %A %B %P'
        run_git_config(f"merge.{strategy}.name", f"merge-policy: {strategy}")
        run_git_config(f"merge.{strategy}.driver", driver_cmd)
        run_git_config(f"merge.{strategy}.recursive", "binary")

    unknown = strategies - BUILTIN_STRATEGIES - CUSTOM_STRATEGIES
    if unknown:
        print(f"warning: unknown strategy names in merge-policy.yaml: {sorted(unknown)}", file=sys.stderr)


def run_git_config(key: str, value: str) -> None:
    subprocess.run(["git", "config", key, value], cwd=REPO_ROOT, check=True)


def main() -> int:
    policy = load_policy()
    update_gitattributes(policy)
    configure_git_drivers(policy)
    print("Merge policy applied. Review the diff to .gitattributes and commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
