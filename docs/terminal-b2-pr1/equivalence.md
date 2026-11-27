# Implementation equivalence and publication ancestry

The publication branch forks **before** the private evidence commit. It does not merge the private branch or mask an ancestor with a deletion. The only differences allowed between this candidate and the owner-reviewed tree are under `docs/terminal-b2-pr1/`.

| Object | Commit | Relationship |
|---|---|---|
| Verified `origin/main` | `7b3c0768ed4bff30b4ef89c8030da458e48a6e47` | Base at preparation time |
| B.2 decision | `6cbd9a35f41a05dddfe018cab2fe6824b277ed98` | Child of verified main |
| Accepted implementation | `80b80214deb90a483c5d1826123202ea104a7eb4` | Child of decision; direct parent of the public-packet candidate |
| Complete private review | `1933ec89c242057c41e23d3ca2de0e7c11ad576a` | **Local-only** sibling of the public packet, not its parent. Retained on `feat/terminal-b2-design-system`. |

## What was compared

All **737 tracked file entries outside the packet**, including their paths, Git object IDs and modes, are identical between the implementation, the private reviewed tree and this candidate. This includes the operator quickstart as well as all production code. The common SHA-256 of the NUL-terminated `git ls-tree -rz --full-tree` records after excluding only the packet prefix is `9d194bff276f05b89e7a5035dd4116f253a8ade246dfc904fbea49d65d49fda0`. The full repository tree is intentionally different: a compact public manifest is not the private collector output, a sanitized log is not its original, and omitted diagnostics are not a new verification run.

| Tracked scope | Files | Identical Git tree or blob |
|---|---:|---|
| `frontend` (including all UI code, configuration, assets, locks and tests) | 179 | `7f8f9739cd50f2bd1a2761f6c90378f55a5af987` |
| `frontend/src` (including 21 `.test.ts` files) | 110 | `d52606f5cb7c584e5c4368ecce0bee7d1b4080ec` |
| `frontend/e2e` (all browser specs) | 17 | `22b6f3dddf7db833794778da0525783132ced25d` |
| `src` | 49 | `392dd9aceb0d3d01c7a866736f73fcb5f1c9b04a` |
| `tests` (including fixtures) | 49 | `d182aabe79fc57711ddd18a169518960cc62fef8` |
| `tools` (including collector and smoke script) | 9 | `43914b23a5f241f377d4cf491035300288f5c5f8` |
| `.githooks` | 1 | `f2c03bfc1eaac45b9b5885dc7c9310262c87446c` |
| `.github` (including CI and ruleset) | 2 | `b415d058eba0f29ffdb493d3ce49405aa759bd12` |
| `uv.lock` | 1 | `85aeff26d7b15305514088b9ad6c2f707013a58d` |
| `frontend/package-lock.json` | 1 | `6d3a304bc4c997741d2654e8642c62bbec25135f` |

For avoidance of doubt, the 737-file comparison includes the root entry point, package manifests, Python constraints and project settings not separately listed. No runtime or test behavior was changed by publication preparation. Equivalence transfers the **scope of the earlier evidence**, not the claim that every check ran again.

## Reproduce the comparison

From the repository root at the candidate, this works **without either private branch**. The first command should print no diff and return zero; the second shows the exact public diff against its verified base.

```sh
git diff --exit-code 80b80214deb90a483c5d1826123202ea104a7eb4 HEAD -- . ':(exclude)docs/terminal-b2-pr1/**'
git diff --no-renames --name-status 7b3c0768ed4bff30b4ef89c8030da458e48a6e47 HEAD
```

For a maintainer with the local archive, repeat the first command with `1933ec89c242057c41e23d3ca2de0e7c11ad576a`. `git rev-parse feat/terminal-b2-design-system` must still return that ID. `git merge-base --is-ancestor 1933ec89c242057c41e23d3ca2de0e7c11ad576a HEAD` must return **1** (not an ancestor); a missing object is **not** a successful ancestry check. The original complete review packet, including the 120-file evidence subtree `d481cdfd55da05f8812e1ada8b1b830219eee8e2`, remains recoverable at that local commit.

- [Public inventory](inventory.txt): **61 changed paths** (41 additions, 20 modifications) relative to verified main, including this report and both inventories.
- [Review differences](review-differences.tsv): **114 packet paths** (8 added, 8 modified, 98 omitted relative to the private review). `A` means public only; `M` means different bytes/mode; `D` means retained only in the private tree. Rename detection is disabled, so a move of an unchanged image appears as `D`/`A`. These are inventory entries, not links promising access to omitted diagnostics.
- [Retention policy](retention.md): why the results, representative pixels and genuine failing assertions are public while the raw corpus, original path-bearing blobs and private transformation mapping are not.

The initial two outgoing commits and their changed blobs were inspected **before** branch creation. Publication acceptance additionally requires scanning every reachable outgoing commit, its metadata and relevant blobs through the new tip, including any earlier versions absent from the tip, not just a clean worktree. The private review/design refs, tags, evidence mapping and archive are not part of that range. An owner decision on the exact new SHA and the required remote checks remain separate.
