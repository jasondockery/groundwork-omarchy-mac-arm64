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
| `Super+Arrow` | Focus the window in that direction, crossing to the monitor on that side at the screen edge |
| `Super+Shift+Arrow` | Swap the current window with the one in that direction, including one on the monitor on that side |
| `Alt+Tab` / `Alt+Shift+Tab` | Next/previous window on this workspace |
| `Super+Tab` / `Super+Shift+Tab` | Next/previous workspace on this monitor |
| `Super+1` through `Super+9`, and `Super+0` for workspace 10 | Select workspace on this monitor |
| `Super+Shift+1` through `Super+Shift+9` | Move the current window to that workspace on this monitor |
| `Super+Shift+O` | Open Obsidian |

Stock directional desktop navigation uses arrows. To cycle without reaching for them, use `Alt+Tab` for windows or `Super+Tab` for workspaces. `Super+H/J/K/L` is not a stock directional keymap: `Super+K` already opens help and `Super+L` cycles the workspace between dwindle tiling, scrolling, and floating. This layer preserves those bindings. On Apple keyboards attached to this machine, `Super` is Command and `Alt` is Option.

Omarchy 4.5 gives every monitor its own workspaces 1 through 10, so the workspace keys above act on the monitor you are using. With one display they behave as before. It also moved shortcuts you may have learned on 4.0: Calculator is `Super+Shift+Q`, Activity is `Super+Shift+T`, Network is `Super+Ctrl+N`, themes are `Super+Ctrl+T`, backgrounds are `Super+Ctrl+W`, and `Super+Alt+Space` opens Apps from the dock. Night Light no longer has a shortcut. For notifications, `Super+Comma` acts on the newest one instead of only dismissing it, `Super+Alt+Comma` dismisses the last one, `Super+Ctrl+Comma` hands you keyboard control of them, and silencing moved to `Super+Ctrl+Alt+Comma`. In a terminal, `Super+C` and `Super+V` send `Ctrl+Shift+C` and `Ctrl+Shift+V`. Edge builds can still report a 4.0-style version string, so press `Super+K` for the list your installed build actually has.

## Apple Screen Sharing

Apple's Screen Sharing app sends Command as Alt and Option as Super. That mapping exists so a Mac on the other end of the old VNC keyboard message sees the key the viewer pressed. WayVNC looks the symbols up in the keymap it publishes on its own virtual keyboard and forwards the modifier mask from that keymap. This service's private XKB option keeps the symbols, so the lookup still finds the key, and changes the modifier each key sets: Command sets Super and Option sets Alt. Shift and Control stay Shift and Control. Filling the viewer's display does not change the symbols. A keyboard attached to the Omarchy machine, including one remapped by keyd, keeps Omarchy's keymap.

| From Screen Sharing | Action |
|---|---|
| `Command+1` through `Command+9` | Select workspace on this monitor |
| `Command+Shift+1` through `Command+Shift+9` | Move the current window to that workspace on this monitor |
| `Command+Tab` / `Command+Shift+Tab` | Next/previous workspace on this monitor |
| `Option+Tab` / `Option+Shift+Tab` | Next/previous window on this workspace |
| `Command+Minus` | Expand the window left |
| `Command+Equal` | Shrink the window left |
| `Command+Shift+Equal` | Expand the window down |
| `Command+Ctrl+Z` | Zoom the screen in; press again to zoom further |
| `Command+Ctrl+Option+Z` | Zoom fully out |
| `Option+1` through `Option+9` | Select the tmux window or Herdr tab |

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

References: [Omarchy hotkeys](https://github.com/omacom/omarchy/blob/quattro/manual/07-hotkeys.md), [Omarchy tmux config](https://github.com/omacom/omarchy/blob/quattro/config/tmux/tmux.conf), [Omarchy Herdr config](https://github.com/omacom/omarchy/blob/quattro/config/herdr/config.toml), and [T3 Code keybindings](https://github.com/pingdotgg/t3code/blob/main/docs/user/keybindings.md). Bindings reviewed on 2026-10-28 against Omarchy v4.5.0-rc2 (a release candidate; the installed edge build may differ), whose release notes list the moved shortcuts. `omarchy update` owns future changes.
