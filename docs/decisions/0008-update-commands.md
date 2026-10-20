# 0008. One routine maintenance command

Use `update-all` for routine maintenance and `groundwork-update` for configuration
only. Omarchy continues to own the OS through `omarchy update`; Groundwork owns
its personal settings, T3 Nightly, and its installed Bitwarden user Flatpak.

The stable installed Python coordinator fetches a unique candidate ref, verifies
a fast-forward from the clean tracked source, and displays its change summary
before advancing. `--check` ends before advancement, because Stow links expose
checkout changes immediately. The apply stage runs the candidate's fresh
`scripts/apply-layer.sh`. Setup uses the same apply code but prints onboarding
and explicitly reconciles T3 Nightly installation; routine apply reuses an
existing Nightly CLI and omits onboarding. No updater hook is added: its outcome
must be reported independently rather than swallowed by Omarchy's hook runner.

Setup stores source path, keyd/screen-sharing choices, selected vault, and the
applied keyd digest locally. Bare reruns preserve them; explicit setup options
change choices. Older installations repeat exclusions once during transition.
The optional Core reference remains pinned and is never a setup prerequisite.

An advisory file lock serializes Groundwork commands. Child stages have deadlines
and their own process groups. Failed stages exit nonzero, preserve prior success
receipts, report completed work, and support retry after source advancement.
Configuration-only receipts are separate from full-maintenance receipts. Package
commands completing do not establish app sign-in, physical input acceptance, or
cross-machine access. These remain first-run acceptance.
