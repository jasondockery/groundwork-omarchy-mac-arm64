# 0025. Tailscale through Omarchy's service helpers

Checked 2026-11-12 against Omarchy `quattro` `bin/omarchy-install-service-tailscale`, `bin/omarchy-installed-service-tailscale`, and `bin/omarchy-remove-service-tailscale`. Those files were read from the published tree. They were not executed on this Mac.

## Omarchy first

| Path | Result |
|---|---|
| `omarchy-install-service-tailscale` | Adds `tailscale` with `omarchy-pkg-add`, enables `tailscaled.service`, runs `sudo tailscale up --accept-routes`, sets `--operator` to the current user, enables `omarchy-tailscale-receive.service`, enables the `omarchy.tailscale` bar plugin, and installs the Admin Console web app. |
| `omarchy-installed-service-tailscale` | Exits 0 when `tailscale status --json` succeeds, or when `tailscaled` is active or its process is running. That is the daemon, earlier than the operator, Taildrop, bar plugin, and Admin Console app. |
| `omarchy-remove-service-tailscale` | Brings Tailscale down and removes the package, receive service, plugin, and web app. Setup does not call it. |

`tailscale up --accept-routes` waits for a person when the machine is logged out. Setup calls `omarchy-installed-service-tailscale`, then the same package, daemon, operator, receive, plugin, and web app commands. It leaves `tailscale up` for you.

## Decision

- **On by default.** `./install.sh` and `update-all` call `omarchy-installed-service-tailscale`. When it reports the service missing, setup runs `omarchy-pkg-add tailscale` and `sudo systemctl enable --now tailscaled.service`. When `tailscale status --json` reports `BackendState` `Running`, setup sets the operator, enables the user receive service, enables the bar plugin, and installs the Admin Console app. Each of those is skipped when it is already in place, so a later run changes nothing.
- **Sign-in stays yours.** Setup never runs `tailscale up`. A machine that still needs login gets `sudo tailscale up --accept-routes --timeout=5m`, then a successful setup exit. After that login, `groundwork-update` finishes the four follow-up steps. A check that does not finish within 15 seconds leaves Tailscale unchanged and prints the same command.
- **Opt out like the other setup choices.** `--without-tailscale` saves `"tailscale": false` in `~/.config/groundwork/omarchy.json` and leaves an existing install alone. Only the opt-out is stored. `--with-tailscale` removes the key. A file written before this choice existed stays on.
- **Nothing private is stored.** No auth key, tailnet policy, or account id is written. Status and prefs are read only to choose the next step.
- **Screen sharing and Tailscale SSH stay as they are.** Setup still does not run `tailscale set --ssh`. The screen-sharing service and its firewall rule are unchanged.

## Not done

- No live tailnet login in this change. Package and helper fixtures are not a signed-in tailnet.
- `groundwork-linux` is unchanged. Its `docs/tailscale.md` owns Homebrew and distro-package setup and says Omarchy has its own repo. It does not ship `omarchy-install-service-tailscale`.

Recheck on 2026-12-12, or when Omarchy changes those three helpers.
