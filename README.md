# quartermaster

Snapshot of Ryan's "router core": `seat-usage` (reads per-seat/pool usage for Claude, Grok, Cursor and related pools, with a cache) plus the `seat-route-hook` Claude Code hook that uses it to route Agent/Bash launches. Extracted as-is from his dotfiles. Not a polished or portable project.

## Layout

| Path | Role |
| --- | --- |
| `bin/seat-usage` | the command (byte-identical to dotfiles `dot_local/bin/executable_seat-usage`) |
| `hooks/seat-route-hook` | Claude Code PreToolUse hook (byte-identical to dotfiles `dot_claude/hooks/executable_seat-route-hook`) |
| `tests/test_seat_usage.py` | test suite (only the two path constants changed, see Tests) |
| `renewals.example.json` | placeholder for `~/.config/seat-usage/renewals.json` (same schema, placeholder values) |
| `install.sh` | links or copies the two commands into the frozen paths |

## Installed-path contract

The command name stays `seat-usage`; council hardcodes it. Install locations are unchanged:

- `~/.local/bin/seat-usage`
- `~/.claude/hooks/seat-route-hook`
- `~/.cache/seat-usage/latest.json`
- `~/.config/seat-usage/renewals.json` (real file is private; see `renewals.example.json`)
- `~/.local/state/seat-usage/asks.jsonl` and `history.jsonl`
- `~/.local/state/seat-usage/runs.jsonl` is written only by council's `council.sh`, not by this code. Override with `SEAT_USAGE_RUNS`.

## Claude Code registration (owned by dotfiles)

This project only documents what it expects in `~/.claude/settings.json`; the file itself stays in dotfiles:

- `PreToolUse`, matcher `"Agent|Bash"`, running `~/.claude/hooks/seat-route-hook`, timeout 50
- `SessionStart`, running `seat-usage --cached`

## Install this checkout

`install.sh` points `~/.local/bin/seat-usage` and `~/.claude/hooks/seat-route-hook` at this checkout. The default is a symlink, so a later `git pull` updates those commands. The script does not edit `~/.claude/settings.json`. Dotfiles still owns that file.

Run it from the checkout:

    ./install.sh

`--prefix` sets the install root. When you omit `--prefix`, the root is `HOME`.

Pass `--copy` to copy the files instead of linking them. Use `--copy` when the system cannot create a symlink.

Before the script replaces a file that this checkout did not install, it renames that file by adding `.pre-quartermaster` to the path. A second run leaves that backup in place.

`--dry-run` prints each action and writes nothing.

To put the previous files back, run:

    ./install.sh --uninstall

Uninstall removes the symlinks or copies that came from this checkout. It moves each `.pre-quartermaster` file back to its original path.

To verify both paths, run:

    ./install.sh --check

`--check` exits 0 when both paths resolve to this checkout and are executable. It exits non-zero otherwise.

If `~/.config/seat-usage/renewals.json` is missing, the script prints a hint that names `renewals.example.json`. It does not create, change, or delete that file.

On Windows, run the script from Git Bash. Command Prompt and PowerShell cannot run it.

## Update on use

A run from a clean checkout of `main` fetches and fast-forwards at most once every 24 hours, then starts again so the new code runs. The timestamp is `~/.cache/quartermaster/seat-usage-update`, beside Conclave's `muse-usage.json`. The command prints `[seat-usage] updated` when the checkout moves.

The check does not stash, reset, or overwrite local changes. A dirty tree, a branch other than `main`, a detached HEAD, a missing network, or any git error skips it and prints nothing. `--route`, `--quiet`, `COUNCIL_VOTER=1`, and `CONCLAVE_VOTER=1` skip it. `QUARTERMASTER_NO_UPDATE=1` turns it off. There is no background task.

## Known limits (shipped unfixed)

- Hardcoded paths (see contract above).
- WSL/Windows assumptions, including the `op.exe` WinGet path (`$SEAT_USAGE_OP` overrides).
- 1Password item path `op://claude/grok web cookie/credential`.
- Fixed Thursday 5:38 PM PT fallback for the Grok reset.
- `ROUTE_RULES` encode dated decisions by Ryan.
- The hook's role mapping depends on specific command shapes.

## Provenance

Snapshot, no history, of `rymiwe/dotfiles` commit `6830ee0dddeb8ef9d544dc78d418e8361e85da5a`.

## Tests

Offline; uses loopback servers and a fake temp credentials file:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_seat_usage.py

At snapshot time this printed `69 passed, 0 failed`. The only change from the dotfiles test is the two path constants (`BIN`, `HOOK`) pointing at `bin/seat-usage` and `hooks/seat-route-hook`. `SEAT_USAGE_BIN` still overrides `BIN`.

`tests/test_contract.py` is a second suite. It calls the CLI and the hook the way a user does. It covers quiet hook failure, the installed paths, cached route output, and renewals. Run it with the same empty environment:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_contract.py

`SEAT_USAGE_BIN` selects the CLI and `SEAT_ROUTE_HOOK` selects the hook. When those variables are unset, both point at the files in this repo. The tests are:

- `hook_prints_route_and_stays_quiet_when_seat_usage_is_missing`
- `hook_prints_route_and_stays_quiet_when_seat_usage_exits_nonzero`
- `hook_prints_route_and_stays_quiet_when_seat_usage_prints_junk`
- `hook_prints_route_and_stays_quiet_when_seat_usage_times_out`
- `hook_prints_route_and_stays_silent_for_council_voter`
- `default_home_receives_cache_asks_history_and_renewals`
- `env_overrides_redirect_cache_asks_and_renewals`
- `cached_lineup_json_names_the_xai_pair`
- `cached_host_json_suggests_grokbot_and_does_not_select`
- `bare_cached_route_prints_the_four_launch_lines`
- `cached_route_json_rows_have_the_hook_keys`
- `cached_is_silent_for_council_voter_and_prints_the_seed`
- `unknown_role_exits_2_with_the_role_list`
- `voter_route_exits_2`
- `example_renewals_schema_sets_the_term_and_a_placeholder_date_does_not`
- `missing_renewals_file_still_prints_the_route`

`tests/test_update.py` runs the command in a temporary git checkout. It covers a fast-forward, the 24 hour stamp, a dirty tree, a non-main branch, a detached HEAD, diverged and ahead history, the opt-out, `--route`, `--quiet`, both voter variables, a failed fetch, and a copy that is not a checkout. Run it with the same empty environment:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_update.py

`tests/test_install.py` runs `install.sh` against a temporary root. It covers a fresh symlink install, `HOME`, a backup of an existing file, a second run, `--dry-run`, `--uninstall`, `--copy`, a broken link, and `renewals.json`. Run it with the same empty environment:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_install.py

## Not in this repo

The status lines (`statusline.py`, `statusline.sh`), `grok-bot-machines`, `grok-engine`, `settings.json` and council handoffs/docs remain in dotfiles or council.

## Usage/routing seam

The seam was analyzed: it is clean and one-way (routing calls about 8 usage functions; usage does not call routing). No split for now; usage and routing stay in one repo.
