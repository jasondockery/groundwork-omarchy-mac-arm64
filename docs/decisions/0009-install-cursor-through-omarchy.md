# 0009. Install Cursor through Omarchy

Omarchy Mac installs Cursor with `omarchy-install-cursor`. On Apple Silicon that command installs the aarch64 build and writes `~/.config/Cursor/argv.json` with `"password-store": "gnome-libsecret"`, which is the keyring Chromium and Electron need under Hyprland. This layer runs that command when `cursor` is not already on `PATH`.

`omarchy pkg add cursor-bin` and `omarchy-install-and-launch Cursor cursor-bin cursor` do not write that setting. This layer does not use them.

An existing `cursor` command stays installed. Setup does not download Omarchy's pinned AppImage over it and does not launch Cursor during `groundwork-update`. If `argv.json` has no `password-store`, setup adds `gnome-libsecret` and leaves the other keys unchanged. A password store that is already present stays as the user wrote it. Comments in that file are accepted. Adding the missing key rewrites the file as JSON. A file that already has the GNOME keyring setting is not rewritten, comments included. A symlink, a non-file, or text that is still not JSON after comments are removed stops setup without rewriting the file.
