# MLS registration patches

These patches target MLS-Bench commit `cfd57a7e0139c72753e32e31bca593719b098717`.

- `mls-registration-clean.patch` registers the external `mls_agent` package in a clean MLS checkout.
- `mls-registration-upgrade-v1.patch` changes the previously installed internal-copy adapter to the external package.

Always record `git rev-parse HEAD`, `git status --short`, the selected patch SHA256 and `git diff --binary` before applying. Run `git apply --check` first. Reversal is `git apply -R --check` followed by `git apply -R`; never use a destructive reset to remove unrelated user changes.
