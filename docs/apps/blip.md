# Blip: agent rules

Moved from `AGENTS.md`. Decision: [0011](../decisions/0011-install-blip-from-git.md). Gateway setup: [iMessage guide](../imessage.md).

- Blip (`nixfred.blip`) is an opt-in iMessage client (`./install.sh --with-blip`, off by default). A default run and `--without-blip` leave an existing Blip install alone.
- Install it from git with `omarchy plugin add`, and move it into the shared plugin list once that list supports git-URL sources and per-entry opt-in.
- The installer does not run `blip-setup` or record a gateway name.
- Its bun preflight downloads the mise-selected `bun-linux-aarch64` release over https, verifies `SHASUMS256.txt`, and calls the T3 `load_alignments` helper before `omarchy-install-dev-env`.
