# 0005. Upstream bugs stay upstream

Bugs in Omarchy Mac are filed on `omacom/omarchy-mac` and left there.

| Topic | Where |
|---|---|
| Black Super+K and Super+Space menu | [Issue #707](https://github.com/omacom/omarchy-mac/issues/707), fix [PR #710](https://github.com/omacom/omarchy-mac/pull/710) |
| `omarchy-asahi-mic.service` missing `/usr/bin/omarchy-hw-apple` | [Issue #728](https://github.com/omacom/omarchy-mac/issues/728) |
| BCM4378 Wi-Fi drops while awake | [Issue #729](https://github.com/omacom/omarchy-mac/issues/729) |

Do not comment on #710 again unless this repo has a new test of that patch. The Mac mini reproduction is already on #707. The `import QtQuick 6.11` pin is not sufficient, and the pull request already says so.

No workaround is applied locally for these three.
