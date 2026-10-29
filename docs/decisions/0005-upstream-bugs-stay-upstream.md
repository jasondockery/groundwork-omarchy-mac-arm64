# 0005. Upstream bugs stay upstream

File bugs where they belong and leave them there. Do not refile them in this repo.

Checked 2026-10-29 against [omarchy-mac-pkgs CONTRIBUTING.md](https://github.com/omacom/omarchy-mac-pkgs/blob/main/CONTRIBUTING.md) (Where a change belongs, and Issues), that repository's README (transitional branches), the issue templates on [omacom/omarchy](https://github.com/omacom/omarchy) and [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac), and [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer).

| Topic | Where |
|---|---|
| Desktop, shell, bindings, or shared helpers | [omacom/omarchy](https://github.com/omacom/omarchy) |
| Apple Silicon packages (`omarchy-mac`, `omarchy-mac-boot`), including Mac hardware services such as Wi-Fi, the microphone, the keyboard, and the boot chain | [omacom/omarchy-mac-pkgs](https://github.com/omacom/omarchy-mac-pkgs) |
| Package recipes, signing, and publication | [omacom/omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) |
| Installer app or install image | [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer) |
| A hardware qualification report, not a defect | [omarchy-m-testing.org](https://omarchy-m-testing.org) |

`omacom/omarchy-mac` still has a bug template and still receives hardware reports. Its `quattro` branch does not take package changes. New package and hardware-service bugs go to omarchy-mac-pkgs. A report already filed on the legacy fork stays there.

| Topic | Current pointer |
|---|---|
| Super+K / Super+Space palette on Qt 6.12 | Fix on this installer/`omarchy-dev` path is [omarchy#14553](https://github.com/omacom/omarchy/pull/14553) in `quattro`. [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707) and [PR #710](https://github.com/omacom/omarchy-mac/pull/710) cover only the omarchy-mac `quattro` path. |
| `omarchy-asahi-mic.service` missing `/usr/bin/omarchy-hw-apple` | [omarchy-mac#728](https://github.com/omacom/omarchy-mac/issues/728). Recheck on Aurora; do not treat the older Asahi result as current. A new report of this kind goes to omarchy-mac-pkgs. |
| BCM4378 Wi-Fi drops while awake | [omarchy-mac#729](https://github.com/omacom/omarchy-mac/issues/729). Recheck on Aurora; do not treat the older Asahi result as current. A new report of this kind goes to omarchy-mac-pkgs. |

Do not comment on #710 again unless this repo has a new test of that omarchy-mac `quattro` patch. The `import QtQuick 6.11` pin is not sufficient, and that pull request already says so. No workaround is applied locally for the mic or Wi-Fi items.
