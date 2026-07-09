# Releasing

This project uses lightweight versioning: **git tags + GitHub Releases**, with
[`CHANGELOG.md`](CHANGELOG.md) as the single human-readable source of truth.
There is no PyPI package — users clone and run — so a version is a *communication*
tool (maturity, release notes, outreach), not a dependency contract.

## Scheme

Semantic-ish, pre-1.0:

- **minor** (`0.X.0`) — new features (e.g. multilingual content, the datasets engine).
- **patch** (`0.x.Y`) — bug fixes and docs-only changes.
- `1.0.0` is reserved for "stable enough to depend on".

## Per-PR discipline

Every PR that changes behavior adds a bullet under `## [Unreleased]` in
`CHANGELOG.md`, in the right group (**Added / Changed / Fixed / Removed**).

Keep the **READMEs out of it** — they link to the changelog; they must never
duplicate the history (and never maintain it in two languages, which only rots).
The version/changelog badges are the only README surface that references it.

## Cutting a release

1. Rename the `## [Unreleased]` heading to `## [X.Y.Z] - YYYY-MM-DD`; leave a
   fresh empty `## [Unreleased]` at the top.
2. Bump `version` in `pyproject.toml` to `X.Y.Z`.
3. Update the link refs at the bottom of `CHANGELOG.md` (`Unreleased` compare +
   the new version's tag link).
4. Commit (`chore(release): vX.Y.Z`), then tag and push:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin master --tags
   ```

5. On GitHub, **Draft a new release** from the tag, let it **auto-generate notes**
   from the merged PRs, and paste the changelog section as the summary.

> The version badge reads the latest tag, so it updates as soon as you push the
> tag — no manual badge edit needed.
