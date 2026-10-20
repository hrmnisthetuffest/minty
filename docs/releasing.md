# Releasing Minty

The updater (`minty update`, and the daily check) looks at the **GitHub Releases** of the repository and compares each release's tag
with the installed version. A tag alone is not enough: there has to be a release, and the release workflow makes it for you.

## Versions

`MAJOR.MINOR.PATCH`, for example `1.0.2`.

- **Patch** (last digit): the normal release, one per push to GitHub.
- **Minor** (middle digit): after a bigger set of changes.
- **Major** (first digit): after a massive change.
- Before the first release the version is `1.0.0-alpha.N`; the first release is **1.0.1**.

The version number changes only when a release is made. Everyday changes are written under `## Unreleased` at the bottom of
`CHANGELOG.md` (newest at the bottom), starting with what changes for the user.

## Making a release (the maintainer)

1. Everything to release is in the development branch, the tests pass and `## Unreleased` describes it.
2. Pick the bump: `python scripts/release_tools.py next patch` shows what the next number would be (`minor`, `major`; `first` for 1.0.1).
3. Set the version in `src/minty_core/__init__.py` and `pyproject.toml`, and turn `## Unreleased` into `## <version> (<date>)`
   (`release_tools.set_version` and `release_tools.finalize_changelog` do both), commit it, and run the whole test suite.
4. `python scripts/leak_check.py --secrets <your keys file> --words <your personal words file>` must say **Clean**.
5. `python scripts/release.py v<version>` checks that the tree is clean, the tag matches the version, the changelog has the heading and the tag is
   new, then prints the two commands that publish:

   ```bash
   git tag -a v1.0.2 -m "Minty 1.0.2"        # add -s to sign it (needed if users set update.verify_signed)
   git push origin main --tags
   ```

6. The release workflow (`.github/workflows/release.yml`) starts on the tag, checks it again and creates the GitHub release with the changelog
   section as its notes. The first 600 characters are what users see in the update notice, so start with what changed for them.

The maintainer's helper (`flow`, kept outside the repository next to the three working folders) does steps 2 to 5 in one command; see its README.

## Contributions

A pull request adds its line under `## Unreleased` and never touches the version. When you merge it, it goes out with the next release.
