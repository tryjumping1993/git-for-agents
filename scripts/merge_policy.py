"""Validated, repository-independent policy loading and path resolution."""
from __future__ import annotations

import argparse
import pathlib
import re
import string
import subprocess
from typing import Any

import yaml

TOOL_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILTIN_STRATEGIES = {"standard", "union"}
CUSTOM_STRATEGIES = {"json-deep-merge", "yaml-deep-merge", "regenerate", "manual-conflict"}
STRATEGIES = BUILTIN_STRATEGIES | CUSTOM_STRATEGIES


class PolicyError(ValueError):
    """An invalid policy; callers must stop before mutating the repository."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently discarding data."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise PolicyError("YAML mapping keys must be strings")
        if key in result:
            raise PolicyError(f"Duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def check_fields(value, allowed, context):
    if not isinstance(value, dict):
        raise PolicyError(f"{context} must be a mapping")
    unknown = value.keys() - allowed
    if unknown:
        raise PolicyError(f"Unknown {context} fields: {', '.join(sorted(unknown))}. "
                          "Use ordered rules; replace legacy exclude entries with explicit later rules.")


def validate_pattern(pattern):
    # A portable subset of Git wildmatch, intentionally rejecting unsupported syntax.
    if (not isinstance(pattern, str) or not pattern or pattern.startswith(("!", "#"))
            or pattern.endswith("/") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '\\[]"' for c in pattern)
            or "//" in pattern or any(p in {".", ".."} for p in pattern.split("/"))):
        raise PolicyError(f"Unsupported pattern {pattern!r}; use /, *, ?, and whole-segment **; directories need /**")
    if any("**" in segment and segment != "**" for segment in pattern.split("/")):
        raise PolicyError(f"** must occupy an entire path segment: {pattern!r}")


def rule_patterns(rule):
    return rule.get("patterns", [rule.get("pattern")])


def validate_policy(policy: Any) -> dict:
    check_fields(policy, {"version", "default_strategy", "default_category", "categories", "rules", "commit_message"}, "policy")
    if type(policy.get("version")) is not int or policy["version"] != 2:
        raise PolicyError("Policy version must be 2; migrate v1 rules to last-match precedence and remove exclude fields")
    policy.setdefault("default_strategy", "standard")
    policy.setdefault("default_category", "other")
    policy.setdefault("categories", {"other": {"description": "Unclassified files", "checks": []}})
    policy.setdefault("rules", [])
    if not isinstance(policy["default_strategy"], str) or policy["default_strategy"] not in STRATEGIES:
        raise PolicyError("Unknown default_strategy")
    categories = policy["categories"]
    if not isinstance(categories, dict) or not categories:
        raise PolicyError("categories must be a nonempty mapping")
    for name, category in categories.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise PolicyError(f"Invalid category name: {name!r}")
        check_fields(category, {"description", "checks"}, f"category {name}")
        if not isinstance(category.get("description", ""), str):
            raise PolicyError(f"category {name} description must be text")
        checks = category.get("checks", [])
        if not isinstance(checks, list) or any(not isinstance(c, str) or not c.strip() for c in checks):
            raise PolicyError(f"category {name} checks must be a list of nonempty strings")
    if not isinstance(policy["default_category"], str) or policy["default_category"] not in categories:
        raise PolicyError("default_category must name a configured category")
    if not isinstance(policy["rules"], list):
        raise PolicyError("rules must be a list")
    for rule in policy["rules"]:
        check_fields(rule, {"pattern", "patterns", "strategy", "category", "command", "reason"}, "rule")
        if ("pattern" in rule) == ("patterns" in rule):
            raise PolicyError("Each rule needs exactly one of pattern or patterns")
        patterns = rule_patterns(rule)
        if not isinstance(patterns, list) or not patterns:
            raise PolicyError("patterns must be a nonempty list")
        for pattern in patterns:
            validate_pattern(pattern)
        if "strategy" not in rule and "category" not in rule:
            raise PolicyError("Each rule needs a strategy and/or category")
        if "strategy" in rule and (not isinstance(rule["strategy"], str) or rule["strategy"] not in STRATEGIES):
            raise PolicyError(f"Unknown strategy: {rule['strategy']!r}")
        if "category" in rule and (not isinstance(rule["category"], str) or rule["category"] not in categories):
            raise PolicyError(f"Unknown category: {rule['category']!r}")
        for field in ("command", "reason"):
            if field in rule and (not isinstance(rule[field], str) or not rule[field].strip()):
                raise PolicyError(f"{field} must be nonempty text")
        if "command" in rule and rule.get("strategy") != "regenerate":
            raise PolicyError("command is only valid for regenerate rules")
    cfg = policy.get("commit_message", {})
    check_fields(cfg, {"title_template", "group_by", "include_stat_counts", "include_strategy_used"}, "commit_message")
    if not isinstance(cfg.get("group_by", "category"), str) or cfg.get("group_by", "category") not in {"category", "file"}:
        raise PolicyError("commit_message.group_by must be category or file")
    for field in ("include_stat_counts", "include_strategy_used"):
        if field in cfg and type(cfg[field]) is not bool:
            raise PolicyError(f"{field} must be a boolean")
    try:
        template = cfg.get("title_template", "Merge branches: {branches}")
        if not isinstance(template, str):
            raise ValueError("non-string title")
        for _, field, spec, conversion in string.Formatter().parse(template):
            if field is not None and (field != "branches" or spec or conversion):
                raise ValueError("unsupported placeholder")
        title = template.format(branches="example")
        if "\n" in title or "\r" in title:
            raise ValueError("multiline title")
    except (AttributeError, KeyError, IndexError, ValueError) as exc:
        raise PolicyError("title_template must be a single line using only {branches}") from exc
    return policy


def load_policy(path: pathlib.Path) -> dict:
    try:
        return validate_policy(yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise PolicyError(f"Cannot load policy {path}: {exc}") from exc


def matches(pattern: str, path: str, *, ignore_case=False) -> bool:
    """Git-style matching, honoring the target repository's core.ignoreCase."""
    if ignore_case:
        lower_ascii = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        pattern, path = pattern.translate(lower_ascii), path.translate(lower_ascii)
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if "/" not in pattern and not anchored:
        path = path.rsplit("/", 1)[-1]
    segments = pattern.split("/")
    parts = []
    for i, segment in enumerate(segments):
        if segment == "**":
            parts.append(".*" if i == len(segments) - 1 else "(?:[^/]+/)*")
        else:
            parts.append("".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in segment))
            if i < len(segments) - 1:
                parts.append("/")
    return re.fullmatch("".join(parts), path, flags=re.DOTALL) is not None


def resolve_path(policy: dict, path: str, *, ignore_case=False) -> dict:
    result = {"strategy": policy["default_strategy"], "category": policy["default_category"]}
    for rule in policy["rules"]:
        if any(matches(pattern, path, ignore_case=ignore_case) for pattern in rule_patterns(rule)):
            if "strategy" in rule:
                result["strategy"] = rule["strategy"]
                result.pop("command", None)
                if "command" in rule:
                    result["command"] = rule["command"]
            if "category" in rule:
                result["category"] = rule["category"]
    return result


def add_repository_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd(), help="Target working tree (default: current directory)")
    parser.add_argument("--policy", type=pathlib.Path, help="Policy path, relative to repository root unless absolute")


def git(repo: pathlib.Path, *args: str) -> str:
    # Decode explicitly: universal-newline translation would corrupt filenames
    # containing carriage returns in NUL-delimited diff output.
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          check=True).stdout.decode("utf-8", errors="surrogateescape")


def repository_context(args):
    repo = pathlib.Path(git(args.repo, "rev-parse", "--show-toplevel").rstrip("\r\n"))
    policy_path = args.policy or pathlib.Path("merge-policy.yaml")
    if not policy_path.is_absolute():
        policy_path = repo / policy_path
    policy_path = policy_path.resolve()
    return repo, policy_path, load_policy(policy_path)


def repository_ignore_case(repo):
    try:
        return git(repo, "config", "--bool", "--get", "core.ignoreCase").strip() == "true"
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:  # unset
            return False
        raise


def resolve_ref(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()


def merge_base(repo, target, branch):
    bases = git(repo, "merge-base", "--all", target, branch).splitlines()
    if len(bases) != 1:
        raise PolicyError("Multiple merge bases require manual analysis; a single-base report would be misleading")
    return bases[0]


def run_cli(main):
    try:
        return main()
    except (PolicyError, OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        import sys
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        print(f"[merge-policy] {detail}", file=sys.stderr)
        return 2
