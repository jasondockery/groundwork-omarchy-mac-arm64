# Learn Omarchy's keys

Omarchy owns the live keymap on this machine. This repo leaves it in place. Practice the same action in tmux and Herdr so moving between agent workspaces does not require a second set of navigation habits. `Super` is the Command key on an Apple keyboard; `Alt` is Option. A prefix sequence means press `Ctrl+Space`, release it, then press the next key.

## Desktop

| Keys | Action |
|---|---|
| `Super+Space` | Omarchy menu |
| `Super+K` | Show desktop bindings |
| `Super+Alt+K` | Show tmux bindings |
| `Super+Ctrl+K` | Show Herdr bindings |
| `Super+Alt+Return` | Open tmux |
| `Super+Ctrl+Return` | Open Herdr |

## tmux and Herdr

tmux sessions correspond to Herdr workspaces; tmux windows correspond to Herdr tabs. Current Omarchy config deliberately aligns their actions:

| Keys | Action in either multiplexer |
|---|---|
| `Ctrl+Space`, then `?` | Show effective bindings |
| `Alt+Enter` | Split below |
| `Alt+Shift+Enter` | Split beside |
| `Alt+Escape` | Close a pane; check for running work before using it |
| `Ctrl+Alt+Arrow` | Focus a pane |
| `Ctrl+Alt+Shift+Arrow` | Resize a pane |
| `Alt+Left` / `Alt+Right` | Previous/next window or tab |
| `Alt+Shift+Left` / `Alt+Shift+Right` | Move the window or tab |
| `Alt+1` through `Alt+9` | Select window or tab by number |
| `Alt+Up` / `Alt+Down` | Previous/next session or workspace |
| Prefix, then `h` / `v` | Split below/beside |
| Prefix, then `c` / `r` / `k` | Create/rename/close window or tab |
| Prefix, then `C` / `R` / `K` | Create/rename/close session or workspace |
| Prefix, then `z` | Zoom pane |
| Prefix, then `d` | Detach, keeping live processes |
| Prefix, then `q` | Reload config |
| Prefix, then `[` | Enter copy mode |

Uppercase letters mean Shift plus the letter. Close actions can terminate work; Omarchy's stock close bindings may act immediately. This repo does not add Groundwork Mac's confirmation dialogs. Use the live help as the authority if the installed version differs. tmux also has `Ctrl+B` as a second prefix; check Herdr's effective help rather than assuming it inherits every tmux alternative.

For shell word movement inside tmux, use `Alt+B` and `Alt+F`; Alt+arrows belong to the multiplexer. Caps Lock held with Space sends the same prefix as physical Control. Do not remap the desktop to imitate Raycast or Karabiner wholesale.

## T3 Code and first practice

T3 Code Nightly runs as `t3` with its local web interface on this ARM64 setup. Use Settings → Keybindings for the commands supported by the installed version. Its `mod` modifier follows the client platform: Control on Linux, Command on macOS. Keep the upstream app conventions and review any deliberate cross-platform remap there; terminal prefixes remain owned by tmux/Herdr. Authenticate an agent provider on the machine where it runs.

Try one short loop: launch Herdr, split below and beside, focus each pane, rename a tab, then detach and return. Repeat in tmux with the same keys. Open T3 Code separately and run a small agent task, then inspect the resulting diff in the terminal. Do not nest multiplexers to learn their keys.

The [original Groundwork curriculum](https://jasondockery.github.io/groundwork/) remains available for shell, Git, and agent-supervision practice. Its older keymap is not the keymap installed here. Mac/Linux/Windows parity and migration work are separate from this native Omarchy setup.

References: [Omarchy hotkeys](https://github.com/omacom/omarchy/blob/quattro/manual/07-hotkeys.md), [Omarchy tmux config](https://github.com/omacom/omarchy/blob/quattro/config/tmux/tmux.conf), [Omarchy Herdr config](https://github.com/omacom/omarchy/blob/quattro/config/herdr/config.toml), and [T3 Code keybindings](https://github.com/pingdotgg/t3code/blob/main/docs/user/keybindings.md). Bindings reviewed on 2026-10-18; `omarchy update` owns future changes.
