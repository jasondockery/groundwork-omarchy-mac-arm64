# 0001. Do not apply Groundwork on Omarchy

`groundwork-mac` remains the setup for Mac, WSL, and containers. On Omarchy, do not run `chezmoi apply` from that repo. The learning curriculum stays in the original [Groundwork site](https://jasondockery.github.io/groundwork/) for now; use [this repo's shortcut guide](../keybindings.md) for the Omarchy keymap. The optional `core/` submodule remains a read-only reference. Do not copy curriculum pages into this repo.

`groundwork-mac` replaces `~/.config/tmux/tmux.conf`, replaces Git config, and switches the login shell toward zsh plus Linuxbrew. Its tmux prefix is now Ctrl+Space, with Ctrl+B as the second prefix, which matches Omarchy. Omarchy still keeps its own tmux file, desktop shortcuts, and shell. Do not copy the Mac tmux config here. User overrides stay in `~/.config`. Leave `/usr/share/omarchy` alone.

Bring over a single habit by hand, into a file this repo already manages, when it is not an Omarchy default.
