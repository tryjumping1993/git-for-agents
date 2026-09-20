# git-for-agents

## Merge policy: combining same-file changes across branches

[merge-policy.yaml](merge-policy.yaml) is a single, human-readable config
that defines how this repo resolves changes made to the *same file* on
different branches, instead of leaving everything to line-based conflict
markers. Per file-pattern rule, it picks a strategy:

| Strategy            | What it does                                                            | Good for |
|----------------------|--------------------------------------------------------------------------|----------|
| `standard`           | Normal git 3-way text merge                                              | source code |
| `union`              | Keep lines added by either side                                          | changelogs, logs |
| `json-deep-merge`    | Merge by key instead of by line; only real key collisions conflict       | `*.json` config |
| `yaml-deep-merge`    | Same as above for YAML                                                   | `*.yml`/`*.yaml` config |
| `regenerate`         | Refuse to hand-merge; tells you the command to regenerate the file       | lockfiles |
| `manual-conflict`    | Always flags a conflict for human review                                 | binary assets |

`json-deep-merge`/`yaml-deep-merge` auto-resolve at the key level: if both
branches only *add* different keys, they combine cleanly. If both branches
only *append* new items to the same list, the new items from both sides are
unioned (deduplicated, order preserved). Anything more entangled — the same
key/list changed to conflicting values, reordering, or one side deleting a
key the other modified — is reported as a conflict (the file is left with
`ours` for that spot so you can review and fix it).

### Setup (once per clone, and after editing merge-policy.yaml)

```sh
pip install -r scripts/requirements.txt
python scripts/setup_merge_drivers.py
```

This updates `.gitattributes` (commit it) and configures the merge drivers
in your local `.git/config`. Every clone/agent needs to run the setup
script once — `.gitattributes` alone can't carry the driver commands.

### Step 0: analyze before merging multiple branches

Before combining several diverged branches into a moving target (e.g. a
`main` that has kept landing its own features/fixes), get a preflight
report of which files overlap and a suggested merge order:

```sh
python scripts/analyze_merge_plan.py main feature/a feature/b feature/c
```

This flags files touched by more than one branch, files that the target
(`main`) *itself* already changed since each branch diverged (the highest-risk
case — it's easy to only compare branches against each other and miss this),
and orders branches so low-risk ones merge first.

### Important: use sequential merges, not octopus

Git's octopus merge strategy (`git merge branchA branchB branchC` in one
command) does **not** invoke custom merge drivers — it aborts to a manual
conflict the moment more than one branch touches the same file, even when
every branch's change is trivially combinable. Merge branches in **one at
a time** instead:

```sh
git merge branchA --no-edit
git merge branchB --no-edit
git merge branchC --no-edit
```

Each sequential merge runs the full merge-policy driver, so independent
same-file changes combine cleanly.

### Human-readable merge commit messages

When combining several branches, generate a summary of what changed where
before committing the merge. Run it against the ref HEAD was at *before*
merging (not a branch that already absorbed the merges):

```sh
git merge --no-commit --no-ff feature/a feature/b feature/c
python scripts/generate_merge_message.py feature/a feature/b feature/c --into HEAD > /tmp/msg.txt
git commit -F /tmp/msg.txt
```

This produces a message grouped by file, e.g.:

```
Merge branches: feature/a, feature/b, feature/c

Combined changes by file:
- README.md: feature/c (+8/-0)  [strategy=standard]
- src/app.py: feature/a (+12/-3), feature/b (+5/-1)  [strategy=standard]
```