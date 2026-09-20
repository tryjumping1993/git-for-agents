# git-for-agents

Repository-independent Git merge policies and review plans for humans and agents.
Use it with application, library, infrastructure, documentation, data, or mixed
repositories. It does not require a particular language, framework, package
manager, directory layout, or hosting provider. Python 3.10+ and Git are required
to run these tools, regardless of the target repository's language.

The policy separates **file purpose** from **merge strategy**. A clean merge is
only a textual or structural result; it is not evidence that a change is correct.

| Review category | Expected scrutiny |
| --- | --- |
| `source` | Thoroughly review intended behavior, contracts, callers, correctness, edge cases, errors, security, compatibility, and relevant performance. Run existing checks and add meaningful regression coverage for behavioral changes. |
| `devops` | Welcome improvements while validating them against the original configuration's operational intent. Check relevant triggers, environments, permissions, artifacts, deployment sequence, and rollback behavior using the project's validation tools. |
| `docs` | Verify guidance and examples against the implementation. |
| `data` | Validate formats, consumers, and reproducibility where applicable. |
| `other` | Establish purpose; use source-level scrutiny for executable or behavioral changes. |

These categories are configurable in [merge-policy.yaml](merge-policy.yaml).
Classification uses paths and filenames as starting hints: a Python deployment
script may be DevOps, while YAML defining application behavior may need source
review. In mixed changes, apply both relevant review checklists. Unknown file
types retain normal Git handling and remain visible in reports.

## Setup

From this repository, install dependencies into your chosen Python environment:

```sh
python -m pip install -r scripts/requirements.txt
python scripts/setup_merge_drivers.py --check
python scripts/setup_merge_drivers.py
```

`--check` validates the policy and prints the resulting attributes without
changing files or Git configuration. Setup writes a managed `.gitattributes`
block and local driver configuration. Review and commit `.gitattributes`.
Every clone needs setup: Git does not distribute driver commands through
attributes. Re-run after changing the policy or moving the tools/interpreter.

For another working tree, copy and tailor `merge-policy.yaml` there, then run:

```sh
python scripts/setup_merge_drivers.py --repo /path/to/target --check
python scripts/setup_merge_drivers.py --repo /path/to/target
python scripts/analyze_merge_plan.py --repo /path/to/target main feature/a
```

All three user-facing commands accept `--repo` and `--policy`. `--repo` defaults
to the current directory and is resolved to its Git working-tree root, including
when run from a subdirectory or linked worktree. `--policy` defaults to that
root's `merge-policy.yaml`; relative policy paths are relative to that root.
An absolute `--policy` can point to a centrally managed policy. Bare repositories
are not supported. Use Windows paths when running on Windows.

The tools can remain outside the target repository; setup records the absolute
tool, policy, and active Python interpreter paths. Those locations must remain
available. Setup never executes regeneration commands or review checks.

## Policy rules

Rules use `pattern` or `patterns` and may specify `category`, `strategy`, or both.
The last matching assignment wins **independently for each field**, following
[Git's attribute precedence](https://git-scm.com/docs/gitattributes). Put broad
rules first and specific exceptions later. Changing category alone preserves a
previously selected strategy. A new strategy also replaces its regeneration
command, preventing an unrelated rule's command from leaking through.

```yaml
version: 2
default_strategy: standard
default_category: source
categories:
  source:
    description: "Thorough source review"
    checks:
      - "Verify contracts, edge cases, and regression coverage."
  devops:
    description: "Validate against existing operational intent"
    checks:
      - "Compare the proposed plan with the existing deployment behavior."
rules:
  - pattern: "**/infra/**"
    category: devops
  - pattern: "data/catalog/*.json"
    strategy: json-deep-merge
  - pattern: "package-lock.json"
    strategy: regenerate
    command: "Run the lockfile generator documented by the owning package."
```

For a pure infrastructure repository, set `default_category: devops`; for an
application with an unusual language or layout, `default_category: source` may
fit better. Monorepos can override categories by package or directory. Update
category `checks` with the repository's actual validation expectations. Checks
are review guidance, not commands executed or verified by this tool.

Patterns support literal paths, `*`, `?`, and whole-segment `**`. A pattern
without `/` matches basenames at any depth. A leading `/` anchors at the root;
`*` does not cross directory separators, while `dir/**` includes descendants.
Matching respects the target repository's `core.ignoreCase` setting. Negation,
character classes, backslash escapes, whitespace in patterns, and trailing
directory slashes are rejected; use a supported broader pattern for filenames
with spaces. Filenames themselves may contain spaces and other Git-supported
characters. Unknown policy fields and duplicate YAML keys fail validation.

On first installation, the managed attributes block goes before existing
attributes so existing project choices, including LFS drivers, retain precedence.
Subsequent runs replace the existing block in place. Root rules after the block,
nested `.gitattributes`, and `.git/info/attributes` can override it. Reports show
the configured policy; inspect the effective Git attribute when overrides matter:

```sh
git check-attr merge git-for-agents-category -- path/to/file
```

## Merge strategies

| Strategy | Behavior when Git invokes a file merge |
| --- | --- |
| `standard` | Restore unspecified merge handling, retaining Git's binary detection and any local `merge.default`. Normally this is Git's three-way merge. |
| `union` | Keep lines from both sides of conflicting regions; ordering and duplicate semantics need review. Opt in only when appropriate. |
| `json-deep-merge` | Three-way key merge for strict JSON. Opt in for known data formats. |
| `yaml-deep-merge` | Three-way key merge for a single JSON-compatible YAML document. Opt in for known data formats. |
| `regenerate` | Leave a conflict with regeneration guidance; no commands run automatically. |
| `manual-conflict` | Leave a conflict and preserve ours for manual resolution. |

Defaults deliberately avoid global JSON/YAML merging or changelog/log union.
Source files, CI definitions, infrastructure configuration, and unfamiliar text
formats use `standard`. Known lockfiles use `regenerate`; common binary assets
use `manual-conflict`, and Git's normal binary detection covers other binaries.
Lockfile generation depends on the package manager and version: reconcile
inputs, use the pinned tools from the owning package directory, inspect the
result, and stage it. Add project-specific generated-file rules as needed.

Structured merging combines independent mapping edits and preserves one-sided
changes. Conflicting scalar edits, deletion versus modification, and different
edits to the same list require resolution. Lists are atomic; simultaneous
appends are **not** unioned. Duplicate keys, malformed input, non-finite numbers,
and unsupported YAML types/tags/documents are rejected. YAML merge keys and
cyclic aliases are unsupported. Accepted YAML follows PyYAML's scalar parsing;
quote strings such as `on`, `off`, and date-like values when needed. YAML comments,
formatting, and alias presentation are not preserved on successful merges.

On a structured conflict or parse error, the working copy remains ours rather
than receiving a partial merge. Git retains the base/ours/theirs index stages;
inspect them, resolve the complete change, and stage the result. During rebases,
Git's meaning of ours/theirs differs from the usual branch-merge perspective.

Drivers run only when Git requires a file-level merge. Fast-forwards, one-sided
changes, and identical edits may bypass them. Neither `manual-conflict` nor
`regenerate` is a universal review gate. Enforce review requirements through
your normal review process and CI.

## Review and merge workflow

Start with a clean working tree and analyze the candidate refs:

```sh
python scripts/analyze_merge_plan.py main feature/a feature/b
python scripts/analyze_merge_plan.py main feature/a feature/b --json
```

The plan lists **all** candidate changed files by category, identifies candidate
overlaps and changes also made on the target since divergence, and includes the
appropriate review checks even without file overlap. Rename sources and
destinations are both listed. The suggested order is an overlap heuristic, not
a dependency analysis or a guarantee of correctness. Unrelated histories and
multiple merge bases require separate analysis. Re-run as the target advances.

Merge one branch at a time so each result can be reviewed and validated. Octopus
merges are not the workflow this tool supports for resolving complex changes.
For each branch, generate the message against the pre-merge target:

```sh
python scripts/generate_merge_message.py feature/a --into HEAD --output merge-message.txt
git merge --no-commit --no-ff feature/a
# Resolve conflicts, perform the category checks, and stage resolved files.
git commit -F merge-message.txt
# Remove the temporary message file, then repeat for feature/b.
```

Stop on merge errors and resolve or abort before proceeding. If Git reports
already up to date, there is no merge commit to create. Avoid staging the
temporary message file. `--output` writes UTF-8 directly on every platform.
Messages group candidate changes by category, distinguish binary changes from
line counts, and label the policy strategy rather than claiming which driver
actually ran. They are not a record of final resolutions or completed checks;
add the actual validation evidence to the commit/PR description.

For a summary spanning several sequential merges, record the original target
commit from `git rev-parse HEAD` before starting, then pass that immutable hash
to `--into`. A branch name that has since advanced cannot represent the original
baseline.

## Migration and verification

Version 1 policies require migration: set `version: 2`, put general rules before
exceptions, replace `exclude` with explicit later rules, and review the broader
JSON/YAML and union defaults. `ours` and `theirs` are not supported strategies.
Run setup again and inspect `.gitattributes`. See [AUDIT.md](AUDIT.md) for the
original defects, fixes, and remaining limits.

```sh
python -m unittest discover -s tests -v
```

Tests exercise real temporary Git repositories, driver invocation, attribute
precedence and case matching, structured data loss regressions, and reporting.
The GitHub Actions workflow runs the suite on Linux, Windows, and macOS with
Python 3.10 and 3.14. The filename test using tabs/newlines runs only on POSIX.
