# AGENTS.md

Rules for AI agents working in this repository (`mcp-harness-io/mcph`).

## Issue tracking

- All work is tracked in GitHub issues. Use the `gh` CLI for all GitHub interactions (issues, PRs, merges).
- Do not start work without an issue. If none exists for the task, create one first with `gh issue create`.

## Workflow

1. **Pick an issue** (or create one first).
2. **Create a branch** from an up-to-date `main`. Reference the issue number in the branch name, e.g. `123-short-description`.
3. **Do the work** on that branch. Never commit directly to `main`.
4. **Create a pull request** with `gh pr create`. Link the issue in the description (e.g. `Closes #123`).
5. **Wait for the user's approval.** Never merge a PR until the user has explicitly approved it. Stop after creating the PR and ask for approval.
6. **Merge the PR to `main`** with `gh pr merge` only after approval, then sync local `main` and delete the branch.

## Commit and PR style

- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages: `<type>: <description>`. Do not use scopes.
- Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`.
- Use the imperative mood and keep the subject line short, e.g. `feat: add tool listing endpoint`.
- Mark breaking changes with `!` after the type (e.g. `feat!: ...`) or a `BREAKING CHANGE:` footer.
- Use the same style for PR titles, since they become the squash-merge commit message.

## Releasing

When the user asks to "do release X.Y.Z", follow this procedure. Releases are published by the `Release` GitHub Actions workflow ([.github/workflows/release.yml](.github/workflows/release.yml)) when a version tag is pushed. It builds the package, publishes to PyPI via trusted publishing, then creates a GitHub Release.

1. **Check preconditions.** `main` is clean and up to date, CI is green, and tag `X.Y.Z` does not already exist (`git ls-remote --tags origin`). PyPI versions cannot be reused.
2. **Create an issue** titled `Release X.Y.Z` and a branch `<issue>-release-X.Y.Z` from `main`.
3. **Bump the version** in both places, which must stay in sync:
   - `pyproject.toml` and `uv.lock`: run `uv version X.Y.Z`
   - `src/mcph/__init__.py`: set `__version__ = "X.Y.Z"`
4. **Run `make test`** and make sure it passes.
5. **Open a PR** titled `chore: release X.Y.Z` with `Closes #<issue>`. Wait for the user's approval, then squash-merge it as usual.
6. **Tag the merge commit on `main`.** After syncing local `main`, run `git tag X.Y.Z` and `git push origin X.Y.Z`. Tags have **no `v` prefix** (e.g. `0.0.2`). The tag must exactly match the version in `pyproject.toml`, or the workflow fails.
7. **Watch the workflow** with `gh run watch` (find the run with `gh run list --workflow release.yml`). Jobs: `build`, `publish`, `github-release`. The `publish` job may wait for approval if the `pypi` environment has required reviewers; tell the user if it does.
8. **Verify and report.** Confirm the version is on https://pypi.org/project/mcph/ and the GitHub Release exists (`gh release view X.Y.Z`). If any step fails, report the failure with the log output. Do not delete or move a pushed tag without asking the user.

Pushing the tag publishes to PyPI and is irreversible, so only do it as part of an explicit release request, after the version bump PR is merged.

## Notes

- Keep one issue per branch and one branch per PR.
- Keep PRs focused on the issue they close.
