# 0008. One routine maintenance command

Use `update-all` for routine maintenance and `groundwork-update` for configuration
only. Omarchy continues to own the OS through `omarchy update`; Groundwork owns
its personal settings, T3 Nightly, and its installed Bitwarden and FreeTube user Flatpaks.
`update-all` also updates listed Omarchy shell-plugin checkouts. `groundwork-update`
installs or retires those plugins and leaves their revisions unchanged. See
[decision 0010](0010-omarchy-plugins-from-shared-list.md).

The stable installed Python coordinator fetches a unique candidate ref, verifies
a fast-forward from the clean tracked source, and displays its change summary
before advancing. `--check` ends before advancement, because Stow links expose
checkout changes immediately. The apply stage runs the candidate's fresh
`scripts/apply-layer.sh`. Setup uses the same apply code but prints onboarding
and explicitly reconciles T3 Nightly installation; routine apply reuses an
existing Nightly CLI and omits onboarding. No updater hook is added: its outcome
must be reported independently rather than swallowed by Omarchy's hook runner.

Setup stores source path, keyd/screen-sharing/Blip/local-inference choices, selected vault, and the
applied keyd digest locally. Bare reruns preserve them; explicit setup options
change choices. A setup saved before the Blip choice existed stays off until
`--with-blip`. A setup saved before the local-inference choice existed stays off until
`--with-local-inference`. Older installations repeat exclusions once during transition.
The optional Core reference remains pinned and is never a setup prerequisite.
When screen sharing's unit, helper, or WayVNC config changed and `WAYLAND_DISPLAY`
is unset (SSH or cron), an inactive unit stays stopped and the restart record is
cleared. When `WAYLAND_DISPLAY` is set, the unit restarts even if it is inactive.
The drop warning prints only when a restart actually runs. See
[desktop sharing](../remote-access.md#automatic-desktop-sharing).

An advisory file lock serializes setup and update commands. The apply child
verifies the exact inherited lock descriptor. Interactive stages receive the
foreground controlling terminal and restore it afterward; subprocess groups
remain owned and bounded. A pending keyd apply records the intended and previously
installed owned digests against the source commit so a failed copy/reload can be
retried even when the next fetch delivers another candidate. Failed stages exit
nonzero, preserve prior success
receipts, report completed work, and support retry after source advancement.
Configuration-only receipts are separate from full-maintenance receipts. Package
commands completing do not establish app sign-in, physical input acceptance, or
cross-machine access. These remain first-run acceptance.
