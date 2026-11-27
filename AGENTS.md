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

## Notes

- Keep one issue per branch and one branch per PR.
- Keep PRs focused on the issue they close.
