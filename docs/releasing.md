# Releasing Minty

The updater (`minty update`, and the daily check) looks at the **GitHub Releases** of the repository and compares each release's tag
with the installed version. Alpha and beta releases are pre-releases; the updater counts them. A release that is a draft, or whose tag
is not a version, is ignored.

1. Bump the version in `src/minty_core/__init__.py` (`1.0.0-alpha.N`) and `pyproject.toml` (`1.0.0aN`) and add a `CHANGELOG.md` entry
   with the same heading. `tests/test_version.py` checks that they agree.
2. Run the whole test suite, then `python scripts/release.py v1.0.0-alpha.N`. It checks that the tree is clean, the tag matches the
   version, the changelog has the heading, and the tag does not exist yet; then it prints the commands to run:

   ```bash
   git tag -a v1.0.0-alpha.N -m "Minty 1.0.0-alpha.N"      # add -s to sign it (needed if users set update.verify_signed)
   git push origin main --tags
   gh release create v1.0.0-alpha.N --prerelease --title "Minty 1.0.0-alpha.N" --notes-file notes.md
   ```

3. The release notes (the first 600 characters) are what users see in the notice, so start them with what changed for them.

Nothing here publishes anything by itself. Set the repository's address once (`YOUR-USER` in `README.md`, `install.sh` and the docs).
