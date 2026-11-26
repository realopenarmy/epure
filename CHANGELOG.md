# Changelog

Newest first. Each entry starts with the VERSION it ships in. The first line of each entry is what an installed bot tells its owner when it picks up that version, so keep it to one plain sentence.

## 2026-11-26.3

You can now send photos of a part instead of a CAD file: the bot lists what it sees, asks for the sizes, shows a model to confirm, and then draws it as usual.

- Section 15 (photo mode): a few short rounds of questions, overall size first, then features, material, and tolerances only where they matter.
- Any object of known size in the shot (a coin, a card, a ruler, a battery, a sheet of paper, or anything you give the size of) gives rough estimates. Estimates are marked ≈ on the drawing, with a note, until you confirm them.
- New `epure photo check|build|mark|scale` commands turn a part spec (plates, blocks, L-brackets, shafts and bushings) into a STEP model. The drawing step itself is unchanged.
- Freeform shapes, castings and other parts that don't fit those shapes are out of scope; the bot asks for a CAD model or a 3D scan instead.
- If the bot says photo mode conflicts with its built-in rules, it needs one reinstall from an updated template. The built-in description still says the bot doesn't model parts.

## 2026-11-26.2

You can now send a problem report from this chat: the bot drafts it, shows it, and submits it only after you say yes.

- Section 14: "feedback" / "report a problem", or a repeated failure, starts a draft. Nothing private (chats, names, sheet links, files, or emails) is included unless you paste it in.
- Copies installed before this version still treat feedback as out of scope until they reinstall once, because the built-in floor rules have to allow it.

## 2026-11-26.1

Épure now updates itself from GitHub, so new drawing rules reach you without a reinstall.

- Moved the operating rules from the bot's description into PLAYBOOK.md. No rule was changed.
- Added the broken-view rules for long, thin parts (`break off|on|auto [--keep N]`), which shipped in the drawing tools on 2026-11-25.
- Added zip handling: a .zip with a STEP inside is unzipped and drawn, since phone apps often refuse to attach .STEP files.
- After a pull that changes the tools, the bot re-runs `install.sh` before the next drawing.
- The bot's built-in description keeps only its identity, the floor safety rules and the pull-first rule. Those rules win over the playbook if the two ever disagree.
- Needs one reinstall for copies installed before this version. After that, no reinstall is needed for playbook changes.
