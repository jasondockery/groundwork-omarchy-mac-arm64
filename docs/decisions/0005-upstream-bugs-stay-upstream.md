# 0005. Upstream bugs stay upstream

File bugs where they belong and leave them there. Do not refile them in this repo.

| Topic | Where |
|---|---|
| Desktop, shell, `omarchy-dev` | [omacom/omarchy](https://github.com/omacom/omarchy) |
| Installer app or install image | [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer) |
| Apple Silicon hardware | [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) |

| Topic | Current pointer |
|---|---|
| Super+K / Super+Space palette on Qt 6.12 | Fix on this installer/`omarchy-dev` path is [omarchy#14553](https://github.com/omacom/omarchy/pull/14553) in `quattro`. [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707) and [PR #710](https://github.com/omacom/omarchy-mac/pull/710) cover only the omarchy-mac `quattro` path. |
| `omarchy-asahi-mic.service` missing `/usr/bin/omarchy-hw-apple` | [omarchy-mac#728](https://github.com/omacom/omarchy-mac/issues/728). Recheck on Aurora; do not treat the older Asahi result as current. |
| BCM4378 Wi-Fi drops while awake | [omarchy-mac#729](https://github.com/omacom/omarchy-mac/issues/729). Recheck on Aurora; do not treat the older Asahi result as current. |

Do not comment on #710 again unless this repo has a new test of that omarchy-mac `quattro` patch. The `import QtQuick 6.11` pin is not sufficient, and that pull request already says so. No workaround is applied locally for the mic or Wi-Fi items.
