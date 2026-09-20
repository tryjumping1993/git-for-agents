"""Shared helpers for loading merge-policy.yaml.

Used by both setup_merge_drivers.py and merge_driver.py so the policy file
is parsed the exact same way everywhere.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "merge-policy.yaml"

# Strategies git itself understands natively via .gitattributes alone.
BUILTIN_STRATEGIES = {"standard", "union"}
# Strategies handled by scripts/merge_driver.py and requiring git config.
CUSTOM_STRATEGIES = {"json-deep-merge", "yaml-deep-merge", "regenerate", "manual-conflict"}


def load_policy(path: pathlib.Path = POLICY_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    policy.setdefault("default_strategy", "standard")
    policy.setdefault("rules", [])
    return policy
