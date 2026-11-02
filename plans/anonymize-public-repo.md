# Remove personal details from the public repo

## Problem

The repo is public but its files and git history contain personal details: a Windows user name, the name of a home NAS with share and folder paths, the old and new profile folder names from a profile migration, mentions of a private companion repo, and two personal email addresses on the commits.

## Fix, in two phases

### Phase 1: the current files (this branch)

- Scripts that hard-coded a profile path now build it from `$env:USERPROFILE`.
- Scripts that hard-coded a NAS destination now read it from `local-settings.json` next to the script. That file is ignored by git. A tracked `local-settings.example.json` shows the keys with placeholder values. A script with no destination configured stops with a message that says how to create the file.
- The README and plans use placeholders (`%USERPROFILE%`, `<NAS share>`) instead of real names.
- The repo's own git email is set to the GitHub noreply address.

### Phase 2: the history (separate step, force-push)

- Back up the repo, then rewrite every commit on every branch to replace the remaining names, paths and the private repo name with placeholders and the author emails with the noreply address.
- Verify with a search across all commits that nothing is left, then replace the local repo with the rewritten one.
- Force-push `main` to GitHub, only with Josh's approval. Ask GitHub support to drop cached copies of the old commits.

## Steps

1. Branch `anonymize-paths` from main (done).
2. Edit the scripts, README and plans; add the example settings file and the `.gitignore` entry; document the settings file in the README.
3. Check that no personal details remain in the files.
4. Josh creates `local-settings.json` on Windows and runs the scripts' dry runs to confirm they still work.
5. Merge only with Josh's approval, then start phase 2.
