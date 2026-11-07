# Cast Hub: working rules for Claude Code

Spec: `docs/SPEC.md` (source: `docs/SPEC.pdf`). Visual source of truth: `design/`.

## Privacy rules (non-negotiable)

- Follow "Rule: nothing from this machine goes to GitHub" in `docs/SPEC.md` in every commit.
- Command output, logs, captures and probe results go to `local/` only. Never put them into
  tracked files, docs, commit messages or issues.
- Use placeholders in examples and tests: `<ssid>`, `192.0.2.10` (RFC 5737), `2001:db8::10`,
  `00:00:5e:00:53:01` (RFC 7042). GUIDs only under `tests/fixtures/`, and made up.
- Never run `git push` without asking. List the files and commits being pushed first.
- Never use `git commit --no-verify` or `git push --no-verify`, and never edit
  `.githooks/allowlist.txt`. Only the owner adds allowlist entries.
- If anything machine-specific is found in a tracked file, stop and report it instead of committing.

### Guardrails in this repo

- `.gitignore`: `local/`, real `casthub.toml`, logs, captures, firmware dumps, screenshots.
- `.githooks/pre-commit`, `commit-msg`, `pre-push`: run `.githooks/privacy_scan.py`. It blocks
  MACs, IPs (except documentation ranges), GUIDs outside `tests/fixtures/`, literal PINs and
  passwords, forbidden file types, and every term in `local/denylist.txt`. It also auto-detects
  this machine's hostname, MACs, machine-id, DMI serials and saved Wi-Fi names at run time. PDFs
  are scanned through their text layer and metadata.
- Hooks must be enabled per clone: `git config core.hooksPath .githooks`
- Scan files ad hoc: `python3 -I .githooks/privacy_scan.py --paths <files>`

## Privileged actions

- Never ask for, read, store or pipe the sudo password (no stdin-password flag, no askpass helper).
- Run root commands only through the root helper, which the owner starts once:
  `sudo python3 -I tools/rootd.py`. Then request each action with
  `tools/asroot.py --why "<reason>" -- <command>`. The owner approves each one there.
- Commit any repo script before asking to run it as root, so the helper shows it as unchanged.
- Root-helper state stays out of the repo (`/run/user/<uid>/casthub-root`, `local/root-actions.log`).

## Working rules

- Read `docs/SPEC.md` before starting a milestone. Work on one milestone at a time.
- Never run commands that change firmware, partitions or bootloader entries without asking.
- Take a Btrfs snapshot before installing system packages or editing `/etc`.
- Each milestone ends with its acceptance test passing and a short note in `docs/LOG.md`.
- Stack: Python 3.12+, asyncio, type hints, ruff + pytest. Tests use made-up fixture values.
