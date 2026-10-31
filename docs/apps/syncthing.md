# Syncthing: agent rules

Moved from `AGENTS.md`. Decision: [0013](../decisions/0013-syncthing-shared-folders.md). Shared contract: `groundwork-core/docs/syncthing.md`. Machine steps: [mini checklist](../syncthing-mini-checklist.md).

- Syncthing is a default. `scripts/groundwork-syncthing.py` owns the apply and `groundwork syncthing-pair`.
- Never set `STGUIADDRESS` or `--gui-address`, never leave Syncthing on the upstream `default` listen addresses, never PUT a default folder object over an existing folder, never pause or unpause `groundwork-shared`, and never write a device ID, API key or Tailscale address into this repo.
- The FreeTube wrapper is not implemented yet.
- Tests use a fake Syncthing; they are not proof of the real REST API, 16 KiB pages or cross-machine sync.
