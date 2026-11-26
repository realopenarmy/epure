# Changelog

Newest first. Each entry starts with the VERSION it ships in. The first line of each entry is what an installed bot tells its owner when it picks up that version, so keep it to one plain sentence.

## 2026-11-26.1

Épure now updates itself from GitHub, so new drawing rules reach you without a reinstall.

- Moved the operating rules from the bot's description into PLAYBOOK.md. No rule was changed.
- Added the broken-view rules for long, thin parts (`break off|on|auto [--keep N]`), which shipped in the drawing tools on 2026-11-25.
- Added zip handling: a .zip with a STEP inside is unzipped and drawn, since phone apps often refuse to attach .STEP files.
- After a pull that changes the tools, the bot re-runs `install.sh` before the next drawing.
- The bot's built-in description keeps only its identity, the floor safety rules and the pull-first rule. Those rules win over the playbook if the two ever disagree.
- Needs one reinstall for copies installed before this version. After that, no reinstall is needed for playbook changes.
