# quartermaster

Snapshot of Ryan's "router core": `seat-usage` (reads per-seat/pool usage for Claude, Grok, Cursor and related pools, with a cache) plus the `seat-route-hook` Claude Code hook that uses it to route Agent/Bash launches. Extracted as-is from his dotfiles. Not a polished or portable project.

## Layout

| Path | Role |
| --- | --- |
| `bin/seat-usage` | the command (byte-identical to dotfiles `dot_local/bin/executable_seat-usage`) |
| `hooks/seat-route-hook` | Claude Code PreToolUse hook (byte-identical to dotfiles `dot_claude/hooks/executable_seat-route-hook`) |
| `tests/test_seat_usage.py` | test suite (only the two path constants changed, see Tests) |
| `renewals.example.json` | placeholder for `~/.config/seat-usage/renewals.json` (same schema, placeholder values) |

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

## Not in this repo

The status lines (`statusline.py`, `statusline.sh`), `grok-bot-machines`, `grok-engine`, `settings.json` and council handoffs/docs remain in dotfiles or council.

## Usage/routing seam

The seam was analyzed: it is clean and one-way (routing calls about 8 usage functions; usage does not call routing). No split for now; usage and routing stay in one repo.
