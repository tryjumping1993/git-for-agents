"""Regression tests including Git's actual attribute and merge behavior."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_merge_plan import analyze, changed_files, format_report
from generate_merge_message import build_message, collect_stats
from merge_driver import strategy_json, strategy_yaml, write_result
from merge_policy import PolicyError, load_policy, matches, resolve_path, validate_policy, repository_ignore_case
from setup_merge_drivers import build_gitattributes_block, updated_gitattributes


def policy(rules=None, **kwargs):
    return validate_policy({"version": 2, "categories": {"source": {}, "devops": {}, "other": {}},
                            "rules": rules or [], **kwargs})


class PolicyTests(unittest.TestCase):
    def test_defaults_are_conservative(self):
        cfg = load_policy(ROOT / "merge-policy.yaml")
        cases = {"src/main.rs": "source", "packages/api/service.cs": "source", "query.sql": "source",
                 "app/settings.yaml": "source", ".github/workflows/test.yml": "devops",
                 "apps/service/infra/main.tf": "devops", "deploy/install.py": "devops",
                 "charts/service/values.yaml": "devops", "new-language.xyz": "other", "README.md": "docs"}
        for path, category in cases.items():
            with self.subTest(path=path):
                self.assertEqual(resolve_path(cfg, path), {"strategy": "standard", "category": category})
        self.assertEqual(resolve_path(cfg, "packages/api/package-lock.json")["strategy"], "regenerate")
        self.assertEqual(resolve_path(cfg, "infra/package-lock.json")["category"], "devops")

    def test_committed_attributes_match_policy(self):
        self.assertEqual((ROOT / ".gitattributes").read_text(encoding="utf-8"),
                         build_gitattributes_block(load_policy(ROOT / "merge-policy.yaml")))

    def test_last_match_per_field_and_command(self):
        cfg = policy([{"pattern": "*.json", "strategy": "json-deep-merge", "category": "source"},
                      {"pattern": "package-lock.json", "strategy": "regenerate", "command": "pinned tool"},
                      {"pattern": "infra/**", "category": "devops"},
                      {"pattern": "infra/package-lock.json", "strategy": "standard"}])
        self.assertEqual(resolve_path(cfg, "infra/package-lock.json"), {"category": "devops", "strategy": "standard"})
        self.assertEqual(resolve_path(cfg, "pkg/package-lock.json")["command"], "pinned tool")

    def test_invalid_policies(self):
        cases = [None, [], {}, {"version": 1}, {"version": 2, "default_strategy": "ours"},
                 {"version": 2, "rules": {}}, {"version": 2, "surprise": True},
                 {"version": 2, "categories": {"source": {}}, "default_category": "missing"},
                 {"version": 2, "commit_message": {"include_stat_counts": "yes"}},
                 {"version": 2, "commit_message": {"group_by": []}},
                 {"version": 2, "commit_message": {"title_template": "{unknown}"}}]
        cases.append({"version": 2, "commit_message": {"title_template": "{branches.__class__}"}})
        for value in cases:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                validate_policy(value)
        for rule in [{"pattern": "*.json", "strategy": "bogus"},
                     {"pattern": "*.json", "strategy": "standard", "exclude": ["a.json"]},
                     {"pattern": "dir/", "strategy": "standard"},
                     {"pattern": "[ab].json", "strategy": "standard"},
                     {"pattern": "a b", "strategy": "standard"},
                     {"pattern": "a**b", "strategy": "standard"},
                     {"pattern": "*.json", "category": "unknown"}]:
            with self.subTest(rule=rule), self.assertRaises(PolicyError):
                policy([rule])

    def test_marker_preservation_idempotence_and_validation(self):
        cfg = policy()
        before = "*.txt text eol=lf\n"
        combined = updated_gitattributes(before, cfg) + "*.png -diff\n"
        self.assertEqual(updated_gitattributes(combined, cfg), combined)
        self.assertIn(before, combined)
        from setup_merge_drivers import GITATTRIBUTES_MARKER_START as start, GITATTRIBUTES_MARKER_END as end
        for bad in (start, end, end + "\n" + start, start + start + end + end):
            with self.subTest(bad=bad), self.assertRaises(PolicyError):
                updated_gitattributes(bad, cfg)


class StructuredTests(unittest.TestCase):
    def merge_json(self, base, ours, theirs):
        result, conflicts = strategy_json(*[json.dumps(v) for v in (base, ours, theirs)])
        return json.loads(result), conflicts

    def test_disjoint_keys_and_identical_edits(self):
        self.assertEqual(self.merge_json({"a": 1}, {"a": 2, "b": 3}, {"a": 2, "c": 4}),
                         ({"a": 2, "b": 3, "c": 4}, []))

    def test_falsey_roots_preserved(self):
        for value in (False, 0, [], "", None):
            with self.subTest(value=value):
                result, conflicts = self.merge_json({}, value, {})
                self.assertEqual(result, value)
                self.assertIs(type(result), type(value))
                self.assertFalse(conflicts)

    def test_boolean_number_collision(self):
        self.assertTrue(self.merge_json({"x": 0}, {"x": True}, {"x": 1})[1])
        self.assertTrue(self.merge_json([0], [True], [1])[1])

    def test_delete_modify_conflict(self):
        for base, ours, theirs in [({"x": {}}, {}, {"x": {"new": 1}}),
                                  ({"x": {"a": 1}}, {}, {"x": {"a": 1, "b": 2}}),
                                  ({"x": {"a": 1}}, {"x": {"b": 2}}, {})]:
            with self.subTest(base=base):
                self.assertTrue(self.merge_json(base, ours, theirs)[1])

    def test_unchanged_delete_is_clean(self):
        self.assertEqual(self.merge_json({"x": {}}, {}, {"x": {}}), ({}, []))

    def test_lists_are_atomic(self):
        self.assertTrue(self.merge_json([1], [1, 2], [1, 3])[1])
        self.assertEqual(self.merge_json([1], [1], [2, 1]), ([2, 1], []))

    def test_empty_base_add_add(self):
        result, conflicts = strategy_json("", '{"a": 1}', '{"b": 2}')
        self.assertEqual(json.loads(result), {"a": 1, "b": 2})
        self.assertFalse(conflicts)

    def test_yaml_disjoint_and_falsey(self):
        result, conflicts = strategy_yaml("a: 1\n", "a: 1\nb: 2\n", "a: 1\nc: 3\n")
        self.assertFalse(conflicts)
        self.assertIn("c: 3", result)
        result, conflicts = strategy_yaml("{}", "false", "{}")
        self.assertEqual(result, "false\n...\n")
        self.assertFalse(conflicts)

    def test_malformed_or_ambiguous_structured_data_is_rejected(self):
        for bad in ('{"x": 1, "x": 2}', "NaN", "[Infinity]", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                strategy_json("{}", bad, "{}")
        for bad in ("x: 1\nx: 2", "1: a", "date: 2020-01-01", "---\na: 1\n---\nb: 2", "x: &x [*x]", "x: .inf"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, yaml.YAMLError, RecursionError)):
                strategy_yaml("{}", bad, "{}")


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="git agents '")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo with spaces"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        self.git("init", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, check=True, data=None):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, input=data,
                              capture_output=True, check=check)

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def cli(self, name, *args, check=True, tool_root=ROOT):
        return subprocess.run([sys.executable, str(tool_root / "scripts" / name), "--repo", str(self.repo), *args],
                              cwd=ROOT, env=self.env, capture_output=True, check=check)

    def install(self, cfg):
        self.write("merge-policy.yaml", yaml.safe_dump(cfg, sort_keys=False))
        self.cli("setup_merge_drivers.py")

    def test_git_attribute_parity_case_sensitive(self):
        self.git("config", "core.ignoreCase", "false")
        self.check_attribute_parity()

    def test_git_attribute_parity_case_insensitive(self):
        self.git("config", "core.ignoreCase", "true")
        self.check_attribute_parity()

    def check_attribute_parity(self):
        ignore_case = repository_ignore_case(self.repo)
        paths = ["a.json", "nested/a.json", "nested/deeper/a.json", "package-lock.json", "nested/package-lock.json",
                 "infra/a.json", "infra/nested/a.json", "README.md", "nested/README.md", "src/A.py", "src/a.py",
                 ".github/workflows/ci.yml", "apps/api/infra/main.tf", "dir with space/a.json"]
        patterns = ["*.json", "/*.json", "infra/*.json", "infra/**", "**/a.json", "**/infra/**", "src/?.py", "src/A.py", "**", "a/**/b"]
        for pattern in patterns:
            cfg = policy([{"pattern": pattern, "strategy": "union", "category": "source"}])
            self.write(".gitattributes", build_gitattributes_block(cfg))
            out = self.git("check-attr", "-z", "--stdin", "merge", data=("\0".join(paths) + "\0").encode()).stdout.decode().split("\0")
            for index in range(0, len(out) - 1, 3):
                path, _, attribute = out[index:index + 3]
                with self.subTest(pattern=pattern, path=path):
                    expected = "union" if matches(pattern, path, ignore_case=ignore_case) else "unspecified"
                    self.assertEqual(attribute, expected)
        cfg = load_policy(ROOT / "merge-policy.yaml")
        self.write(".gitattributes", build_gitattributes_block(cfg))
        out = self.git("check-attr", "-z", "--stdin", "merge", "git-for-agents-category",
                       data=("\0".join(paths) + "\0").encode()).stdout.decode().split("\0")
        for index in range(0, len(out) - 1, 3):
            path, attr, value = out[index:index + 3]
            resolved = resolve_path(cfg, path, ignore_case=ignore_case)
            expected = resolved["category"] if attr == "git-for-agents-category" else ("unspecified" if resolved["strategy"] == "standard" else resolved["strategy"])
            self.assertEqual(value, expected, (path, attr))

    def test_setup_check_invalid_and_idempotent(self):
        cfg = policy([{"pattern": "*.json", "strategy": "json-deep-merge"}])
        self.install(cfg)
        attrs = (self.repo / ".gitattributes").read_bytes()
        config = (self.repo / ".git/config").read_bytes()
        self.cli("setup_merge_drivers.py", "--check")
        self.assertEqual((self.repo / ".gitattributes").read_bytes(), attrs)
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)
        self.cli("setup_merge_drivers.py")
        self.assertEqual((self.repo / ".gitattributes").read_bytes(), attrs)
        self.write("merge-policy.yaml", "version: 2\ndefault_strategy: bogus\n")
        self.assertEqual(self.cli("setup_merge_drivers.py", check=False).returncode, 2)
        self.assertEqual((self.repo / ".gitattributes").read_bytes(), attrs)
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)

    def test_actual_merge_handles_tool_and_file_paths(self):
        cfg = policy([{"pattern": "*.json", "strategy": "json-deep-merge"}])
        self.install(cfg)
        tool_copy = Path(self.tmp.name) / "tool kit's $copy"
        shutil.copytree(ROOT / "scripts", tool_copy / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        self.cli("setup_merge_drivers.py", tool_root=tool_copy)
        name = "data/config's $value; space.json"
        self.write(name, '{"base": 1}\n')
        self.commit("base")
        self.git("checkout", "-b", "feature")
        self.write(name, '{"base": 1, "feature": 2}\n')
        self.commit("feature")
        self.git("checkout", "main")
        self.write(name, '{"base": 1, "main": 3}\n')
        self.commit("main")
        merged = self.git("merge", "feature", "--no-edit", check=False)
        self.assertEqual(merged.returncode, 0, merged.stderr.decode())
        self.assertEqual(json.loads((self.repo / name).read_text()), {"base": 1, "feature": 2, "main": 3})

    def test_actual_conflict_preserves_ours_and_stages(self):
        self.install(policy([{"pattern": "*.json", "strategy": "json-deep-merge"}]))
        self.write("data.json", '{"x": {}}\n')
        self.commit("base")
        self.git("checkout", "-b", "feature")
        self.write("data.json", '{"x": {"new": 1}}\n')
        self.commit("feature")
        self.git("checkout", "main")
        ours = b"{}\r\n"
        (self.repo / "data.json").write_bytes(ours)
        self.commit("delete")
        result = self.git("merge", "feature", "--no-edit", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.repo / "data.json").read_bytes(), ours)
        self.assertEqual(len(self.git("ls-files", "--unmerged").stdout.splitlines()), 3)

    def test_regenerate_is_advisory(self):
        self.install(policy([{"pattern": "*.lock", "strategy": "regenerate", "command": "touch should-not-exist"}]))
        self.write("deps.lock", "base\n")
        self.commit("base")
        self.git("checkout", "-b", "feature")
        self.write("deps.lock", "feature\n")
        self.commit("feature")
        self.git("checkout", "main")
        self.write("deps.lock", "main\n")
        self.commit("main")
        result = self.git("merge", "feature", "--no-edit", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"touch should-not-exist", result.stderr)
        self.assertFalse((self.repo / "should-not-exist").exists())

    def test_existing_project_attributes_keep_precedence(self):
        self.write(".gitattributes", "*.bin merge=lfs -text\n")
        self.install(policy())
        self.assertIn(b"merge: lfs", self.git("check-attr", "merge", "--", "asset.bin").stdout)

    def test_manual_driver_does_not_block_one_sided_changes(self):
        self.install(policy([{"pattern": "*.bin", "strategy": "manual-conflict"}]))
        self.write("asset.bin", "base")
        self.commit("base")
        self.git("checkout", "-b", "feature")
        self.write("asset.bin", "feature")
        self.commit("feature")
        self.git("checkout", "main")
        self.write("unrelated.txt", "main")
        self.commit("main")
        self.git("merge", "feature", "--no-edit")
        self.assertEqual((self.repo / "asset.bin").read_text(), "feature")

    def test_invalid_structured_input_leaves_ours_untouched(self):
        self.install(policy([{"pattern": "*.json", "strategy": "json-deep-merge"}]))
        for name, value in (("base", b"{}"), ("ours", b'{"x":1,"x":2}\r\n'), ("theirs", b'{"a":3}')):
            (self.repo / name).write_bytes(value)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/merge_driver.py"), "--strategy", "json-deep-merge",
                                 "base", "ours", "theirs", "data.json"], cwd=self.repo, env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Duplicate JSON key", result.stderr)
        self.assertEqual((self.repo / "ours").read_bytes(), b'{"x":1,"x":2}\r\n')

    def test_failed_output_replacement_preserves_original(self):
        self.write("ours", "original")
        with mock.patch("merge_driver.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                write_result(self.repo / "ours", "replacement")
        self.assertEqual((self.repo / "ours").read_text(), "original")
        self.assertEqual(list(self.repo.glob(".merge-policy-*")), [])

    def test_custom_default_strategy_is_installed(self):
        self.install(policy(default_strategy="json-deep-merge"))
        self.assertIn(b"--strategy json-deep-merge", self.git("config", "--get", "merge.json-deep-merge.driver").stdout)
        self.assertIn(b"merge: json-deep-merge", self.git("check-attr", "merge", "--", "unknown.data").stdout)

    def test_duplicate_policy_key_is_rejected(self):
        self.write("merge-policy.yaml", "version: 2\ndefault_strategy: standard\ndefault_strategy: union\n")
        with self.assertRaises(PolicyError):
            load_policy(self.repo / "merge-policy.yaml")

    def test_missing_ref_and_unrelated_history_fail_cleanly(self):
        self.install(policy())
        self.write("base.txt", "base")
        self.commit("base")
        result = self.cli("analyze_merge_plan.py", "main", "missing", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"Traceback", result.stderr)
        self.git("checkout", "--orphan", "unrelated")
        self.commit("unrelated root")
        self.assertEqual(self.cli("analyze_merge_plan.py", "main", "unrelated", check=False).returncode, 2)

    def test_multiple_branches_and_target_overlap_order(self):
        cfg = load_policy(ROOT / "merge-policy.yaml")
        self.install(cfg)
        self.write("app.py", "base\n")
        self.write("Cargo.lock", "base\n")
        self.commit("base")
        self.git("checkout", "-b", "risky")
        self.write("app.py", "risky\n")
        self.write("Cargo.lock", "risky\n")
        self.commit("risky")
        self.git("checkout", "main")
        self.git("checkout", "-b", "lower")
        self.write("app.py", "lower\n")
        self.commit("lower")
        self.git("checkout", "main")
        self.write("Cargo.lock", "target\n")
        self.commit("target")
        report = analyze(self.repo, cfg, "main", ["risky", "lower"])
        self.assertEqual(report["suggested_order"], ["lower", "risky"])
        items = {f["path"]: f for f in report["files"]}
        self.assertTrue(items["app.py"]["shared"])
        self.assertEqual(items["Cargo.lock"]["target_overlap"], ["risky"])
        self.assertEqual(items["Cargo.lock"]["strategy"], "regenerate")

    def test_analysis_and_message_include_review_without_overlap(self):
        cfg = load_policy(ROOT / "merge-policy.yaml")
        self.install(cfg)
        self.write("app.py", "base\n")
        self.write("old name.txt", "old\n")
        self.commit("base")
        self.git("checkout", "-b", "feature")
        self.write("app.py", "feature\n")
        self.write(".github/workflows/check.yml", "name: ci\n")
        (self.repo / "image.png").write_bytes(b"\0\1\2")
        self.git("mv", "old name.txt", "new name.txt")
        self.commit("feature")
        self.git("checkout", "main")
        report = analyze(self.repo, cfg, "main", ["feature", "feature"])
        self.assertEqual(report["branches"], ["feature"])
        self.assertIn("source", report["review"])
        self.assertIn("devops", report["review"])
        self.assertIn("No file overlap", format_report(report))
        self.assertIn("existing contracts", format_report(report))
        self.assertIn("original operational intent", format_report(report))
        stats = collect_stats(self.repo, ["feature"], "main")
        self.assertIn("old name.txt", stats)
        self.assertIn("new name.txt", stats)
        message = build_message(cfg, ["feature"], stats)
        self.assertIn("(binary)", message)
        self.assertIn("source:", message)
        self.assertIn("devops:", message)
        self.cli("analyze_merge_plan.py", "main", "feature", "--json")
        self.cli("generate_merge_message.py", "feature", "--output", str(self.repo / "message.txt"))
        self.assertEqual((self.repo / "message.txt").read_text(encoding="utf-8"), message)
        self.write("app.py", "main\n")
        self.commit("main")
        report = analyze(self.repo, cfg, "main", ["feature"])
        self.assertEqual(next(f for f in report["files"] if f["path"] == "app.py")["target_overlap"], ["feature"])

    @unittest.skipIf(os.name == "nt", "Windows disallows tabs/newlines in filenames")
    def test_unusual_filenames(self):
        self.write("base.txt", "base")
        self.commit("base")
        base = self.git("rev-parse", "HEAD").stdout.decode().strip()
        name = "tab\tand\nnewline\rand-unicode-λ.txt"
        self.write(name, "one\n")
        self.commit("new file")
        self.assertEqual(changed_files(self.repo, base, "HEAD"), {name})
        self.assertIn(name, collect_stats(self.repo, ["HEAD"], base))


if __name__ == "__main__":
    unittest.main()
