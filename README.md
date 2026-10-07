# Git history migration

Copy an existing GitHub repository into a new **public** organization repository, replacing commit author and committer identities with a selection of authorized GitHub accounts. The source repository is read only.

This is the history-rewriting workflow: it copies existing commits in one operation. It does not develop code, generate 100–200 commits, schedule pushes, transfer issues/PRs/settings, or sign commits. Pushes authenticate as the operator's GitHub CLI account. The configured agent accounts appear in commit metadata; their tokens are not used to push, and metadata attribution does not prove original authorship.

## Files and prerequisites

Put `migrate_history.py` and this `README.md` in your new project repository.

Install Python 3.9+, Git, and the official GitHub CLI (`gh`). Authenticate locally:

```sh
gh auth login
gh auth status
```

The operator needs read access to the source and permission to create and push repositories in the destination organization. The script uses the CLI's existing authentication. Do not commit tokens, private keys, or credential documents. No private signing keys are needed: rewritten commits are unsigned, and invalidated commit/tag signatures are removed.

Keep migration directories **outside this tool repository**. They contain private source history, including original objects no longer referenced by rewritten branches. Do not commit or upload these directories.

## 1. Prepare locally

Example (replace every placeholder with your own values):

```sh
python3 migrate_history.py prepare \
  --source SOURCE_OWNER/PROJECT \
  --destination DESTINATION_ORG/PROJECT \
  --agent ACCOUNT_ONE \
  --agent ACCOUNT_TWO \
  --agent ACCOUNT_THREE \
  --agent ACCOUNT_FOUR \
  --count 4 \
  --workdir ../project-migration
```

Use only account identities whose owners authorize this use. `--agent` is repeatable; `--count` randomly selects 3–8 accounts from that pool. To select all four listed accounts, use `--count 4`. Commits are assigned in rotation among the randomly selected accounts, giving approximately equal shares. Each account's actual GitHub numeric ID is resolved to construct its GitHub noreply email. Usernames are used as display names. The ACCOUNT_* examples are placeholders; replace them with actual usernames.

The working directory must not already exist. Preparation reads GitHub but creates nothing there. It copies all branches and tags locally, rewrites commits, verifies each unchanged code tree and rewritten object, checks commit count and object integrity, and writes `manifest.json`.

Preserved: code trees, executable bits and tracked files, commit messages and dates, merge parent relationships, branch/tag names. Commit hashes change, including descendants. Annotated tag targets change; tagger identity is retained, signatures are removed. Unsupported refs cause a stop instead of being silently discarded. Submodule URLs and commit IDs remain unchanged. Git LFS pointer files are preserved, but LFS payloads are **not migrated**; repositories using LFS require a separate migration before they are complete.

## 2. Review

Open `../project-migration/manifest.json`. Confirm:

- Source and destination names are correct.
- Account selection and commit counts are correct.
- All listed branches/tags and their reachable history are suitable for public release.
- Public exposure is intended, including any sensitive data in historical commits.

Preparation does not scan history for secrets. If history needs redaction, stop and address that separately before publishing. The generated manifest records the exact account assignments and original-to-rewritten hash mapping; publishing does not rerandomize them.

Optional local inspection:

```sh
git --git-dir=../project-migration/rewritten.git log --all --format='%h %an <%ae> %s' -10
git --git-dir=../project-migration/rewritten.git fsck --no-reflogs
```

## 3. Publish explicitly

This creates a new **public** repository and pushes the prepared branches and tags immediately:

```sh
python3 migrate_history.py publish \
  --workdir ../project-migration \
  --confirm-public
```

The destination must not exist. The script refuses existing repositories, never force-pushes, and never changes the source. It creates the destination, atomically pushes explicit refs, sets the source's default branch as the destination default, and compares the remote refs against the manifest. Success writes `published.json`.

GitHub repository creation and pushing are separate operations. If creation succeeds but pushing fails, the destination can be left empty or partially configured. **Do not blindly rerun or delete it**: inspect the destination and error locally. The script intentionally stops when a repository already exists; automatic resume/cleanup is not implemented. A failure after a successful push may mean the code is present but default-branch configuration or final verification failed.

## 4. Verify in the browser

Open the destination URL in a signed-out/private browser window. Confirm the **Public** badge, files, branch list, commit count and history. Open several commits and confirm GitHub links the author and committer to the configured accounts. Inspect the organization page to confirm the repository is listed publicly. GitHub contributor summaries may update later; inspect individual commits for attribution.

For the next project, repeat preparation with its source/destination and a new working directory. All destinations created by this tool are public.

## Limitations and validation

This tool changes attribution metadata; it does not simulate separate pushes by those accounts or backdate new activity. Original dates are retained. It does not migrate releases, issues, PRs, Actions secrets, permissions, branch protections, LFS payloads or submodule repositories. It requires a nonempty source and at least as many commits as selected accounts.

Local validation for this delivered version covered a fixture repository with branches, a merge, lightweight/annotated tags, preserved trees/messages/dates/parent topology, and unchanged source refs. No live repository was created or pushed while preparing this deliverable. Production GitHub permissions and account attribution must be verified by the operator.
