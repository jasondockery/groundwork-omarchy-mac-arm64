# 0006. No personal accounts in the repo

The files in the tree stay free of account material. Git history has to stay that way too. Do not publish the repo while an old commit still contains keys, an email address, or an org remote.

The tree does not contain private keys, public keys, account emails, employer Git remotes, Wi-Fi names, or Tailscale addresses. SSH host blocks and any work-org `insteadOf` rule stay on the machine that needs them.

`git/config.local.example` is a placeholder. `./install.sh` does not copy it. On a machine, copy it to `~/.config/git/config.local`, fill it in, and include that path from that machine's Git config. The filled-in file is gitignored if it lands in the repo by mistake.
