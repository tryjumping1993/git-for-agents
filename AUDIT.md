# Codebase audit

The audit covered all five scripts, the policy, generated attributes, and README.
The resulting design applies to mixed and single-purpose repositories, with
deeper source review and DevOps validation against original operational intent.

| Finding | Impact | Resolution |
| --- | --- | --- |
| First-match Python rules disagreed with last-match Git attributes; matchers also disagreed on basenames and exclusions. | Reports could describe a driver Git would never run; the lockfile rule was overwritten by a text exclusion. | Shared validated resolution, ordered per-field rules, explicit exceptions, actual Git attribute parity tests, and regenerated attributes. |
| Default strategy was reported but never installed; standard exceptions were skipped. | The policy's stated behavior was unreliable. | Emit the default and explicit standard resets; register custom default drivers. |
| `loader(text) or {}` erased false, zero, null, strings, and empty lists. Python equality conflated booleans and numbers. | Structured merges could silently change data or accept conflicting edits. | Preserve parsed roots and use type-aware recursive equality. |
| Missing mappings became empty mappings during recursion. | Deleting an object while another branch added keys could silently resurrect or discard data. | Treat deletion versus modification as a conflict before recursing. |
| Duplicate structured keys were accepted by parsers. | Earlier values could disappear without review. | Reject duplicate keys, malformed data, unsupported YAML structures, and non-finite numbers; preserve ours on conflict/error. |
| Most JSON/YAML and all changelogs/logs got aggressive merge defaults. | CI, deployment, configuration, or ordered data could merge cleanly without preserving behavior. | Standard defaults; structured and union strategies require opt-in. Lists are atomic, correcting the README's unimplemented append-union promise. |
| No source/DevOps review distinction. | File extension dictated mechanics without stating validation expectations. | Independent configurable categories and review checklists, displayed even when files do not overlap. Unknown types stay visible. |
| Repository paths were fixed relative to the scripts; the driver used an unqualified `python`. | Reuse against other repositories and Python environments was unreliable. | `--repo`/`--policy`, working-tree discovery, active interpreter registration, and shell-safe executable/path handling. |
| Setup appended generated defaults after project attributes. | Existing merge drivers such as LFS could be displaced on adoption. | New blocks precede existing attributes; updates preserve block location and surrounding content. Override limits are documented. |
| Policy errors were not validated before mutation. | Typos, unsupported strategies, or malformed generated blocks could leave misleading setup. | Strict schema and marker validation, actionable failures, and non-mutating `--check`. |
| Git filenames were parsed by lines; binary stats became zero. | Spaces/quoting, tabs, newlines, and renames could produce incorrect reports. | NUL-delimited output without newline translation, explicit rename endpoints, escaped display paths, and binary labels. |
| Reports declared no-overlap branches safe in any order, and manual strategies always conflicting. | Users could mistake path overlap for semantic safety or assume all changes invoke drivers. | Qualified overlap heuristic, target-overlap accounting, review guidance for all files, and documented driver invocation limits. |
| README recommended an octopus merge while prohibiting it elsewhere; messages implied actual merge results. | The documented workflow contradicted itself and could misrepresent validation. | Sequential per-branch workflow, retained target commit guidance, UTF-8 output option, and candidate-change labels. |
| No tests or automated checks existed. | Merge corruption and platform differences had no regression protection. | Unit tests plus actual Git integrations, with a Linux/Windows/macOS CI matrix. |

## Verification and limits

Local validation uses Python 3.14 and Git 2.49 on Windows. Tests verify actual
driver execution with spaces, apostrophes, dollar signs, and semicolons in paths;
conflicts preserve ours and all Git stages. Attribute tests cover both settings
of `core.ignoreCase`. The suite also validates policy failure behavior, source
and DevOps classification, reports, and structured merge regressions. CI is
configured for other platforms; local execution does not establish their result.

The policy is a review aid, not semantic analysis or an approval gate. A project's
existing intent must be established from its code, documentation, configuration,
and requested behavior. Validation guidance is not automatically executed or
attested. Reports describe configured policy; nested/local attributes and Git
configuration can override it. Source checks must examine cross-file and
cross-branch interactions even when Git reports no textual conflict.

The supported pattern subset is intentionally validated; it does not implement
every Git wildmatch feature. Structured merging supports strict JSON and a
single JSON-compatible YAML document, with reserialization limitations documented
in the README. Custom drivers are only invoked for file-level merges. External
tool and interpreter paths must remain available, and setup must be rerun after
policy or path changes. Bare repositories and ambiguous multiple-base histories
are outside the supported workflow.

Git matching, driver contracts, and override behavior were checked against the
[official gitattributes documentation](https://git-scm.com/docs/gitattributes)
and exercised against Git itself.
