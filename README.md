# quartermaster

Snapshot of Ryan's "router core": `seat-usage` (reads per-seat/pool usage for Claude, Grok, Cursor and related pools, with a cache) plus the `seat-route-hook` Claude Code hook that uses it to route Agent/Bash launches. Extracted as-is from his dotfiles. Not a polished or portable project.

## Layout

| Path | Role |
| --- | --- |
| `bin/seat-usage` | the command |
| `config/pools.json` | pool records, role defaults, and score weights. Model ids live here |
| `config/priors.example.json` | shape a weekly job can write as `config/priors.json` |
| `hooks/seat-route-hook` | Claude Code PreToolUse hook (byte-identical to dotfiles `dot_claude/hooks/executable_seat-route-hook`) |
| `tests/test_seat_usage.py` | test suite (only the two path constants changed, see Tests) |
| `renewals.example.json` | placeholder for `~/.config/seat-usage/renewals.json` (same schema, placeholder values) |
| `install.sh` | links or copies the two commands into the frozen paths |
| `bin/seat-usage-push` | optional: copies an allowlist of the cache to a secret gist for the phone widget |
| `systemd/` | the `seat-usage-push` timer (every 5 minutes) |
| `android/` | Seats, the Android home-screen widget that reads that gist |

## Installed-path contract

The command name stays `seat-usage`; council hardcodes it. Install locations are unchanged:

- `~/.local/bin/seat-usage`
- `~/.claude/hooks/seat-route-hook`
- `~/.cache/seat-usage/latest.json`
- `~/.config/seat-usage/renewals.json` (real file is private; see `renewals.example.json`)
- `~/.local/state/seat-usage/asks.jsonl` and `history.jsonl`
- `~/.local/state/seat-usage/outcomes.jsonl` (optional extra track record; `SEAT_USAGE_OUTCOMES`)
- `~/.local/state/seat-usage/runs.jsonl` is council's run log. This command reads it and does not write it. Override with `SEAT_USAGE_RUNS`.

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

The check does not stash, reset, or overwrite local changes. A dirty tree, a branch other than `main`, a detached HEAD, a missing network, or any git error skips it and prints nothing. `--route`, `--quiet`, `--cached`, `COUNCIL_VOTER=1`, and `CONCLAVE_VOTER=1` skip it. `QUARTERMASTER_NO_UPDATE=1` turns it off. There is no background task.

## Phone widget (optional)

`bin/seat-usage-push` copies an allowlist of `~/.cache/seat-usage/latest.json` into a secret gist: percent, reset time, window, exhausted flag and reading time per seat, in the status line's pool order and hues. No token, cookie, plan text or `lines` leaves the machine. Anyone holding the gist id can read those numbers. It refreshes a cache older than 10 minutes under the status line's lock, then pushes when the payload changed, and at least every 30 minutes as a heartbeat.

`android/` is Seats, a home-screen widget that draws one row per seat in the status line's order. The phone never reaches the desktop: it reads the gist from `api.github.com` when Android's 30-minute update fires or when you tap it, and keeps the last good copy for when there is no network. `Seats.kt` holds the rules and is unit tested (`cd android && ./gradlew testDebugUnitTest`).

Desktop setup. `install.sh` does not install these; link them by hand:

    ln -s "$PWD/bin/seat-usage-push" ~/.local/bin/seat-usage-push
    gh auth refresh -s gist                   # the gh token needs the gist scope
    seat-usage-push --setup                   # creates the gist, prints its id
    systemctl --user link "$PWD/systemd/seat-usage-push.service" "$PWD/systemd/seat-usage-push.timer"
    systemctl --user enable --now seat-usage-push.timer
    seat-usage-push --dry-run                 # print the payload, push nothing

Phone setup: install `seats.apk` from the `seats-latest` release, add the Seats widget, and paste the gist id or URL. CI (`.github/workflows/seats-widget.yml`) rebuilds that release on every push to `main` that touches `android/`.

Signing: this repo is public, so the key is not checked in. CI decodes it from the `SEATS_KEYSTORE_B64` secret, with its password in `SEATS_KEYSTORE_PASSWORD`, so each build installs over the last. Without those secrets (a fork, or a local build) the APK gets the Android debug key, and `main` publishes no release.

Tests: `python3 tests/test_seat_usage_push.py`.

## Known limits (shipped unfixed)

- Hardcoded paths (see contract above).
- WSL/Windows assumptions, including the `op.exe` WinGet path (`$SEAT_USAGE_OP` overrides).
- 1Password item path defaults to `op://claude/grok web cookie/credential` (`$SEAT_USAGE_OP_REF` overrides).
- Fixed Thursday 5:38 PM PT fallback for the Grok reset.
- Council lineup and host order still name families in code. Launch routing does not.
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

`tests/test_update.py` runs the command in a temporary git checkout. It covers a fast-forward, the 24 hour stamp, a dirty tree, a non-main branch, a detached HEAD, diverged and ahead history, the opt-out, `--route`, `--quiet`, `--cached`, both voter variables, a failed fetch, and a copy that is not a checkout. Run it with the same empty environment:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_update.py

`tests/test_install.py` runs `install.sh` against a temporary root. It covers a fresh symlink install, `HOME`, a backup of an existing file, a second run, `--dry-run`, `--uninstall`, `--copy`, a broken link, and `renewals.json`. Run it with the same empty environment:

    env -i PATH=/usr/bin:/bin HOME=/tmp/emptyhome PYTHONDONTWRITEBYTECODE=1 python3 tests/test_install.py

## Not in this repo

The status lines (`statusline.py`, `statusline.sh`), `grok-bot-machines`, `grok-engine`, `settings.json` and council handoffs/docs remain in dotfiles or council.

## Routing

`seat-usage --route` still prints one line per role:

```
route: <role> | <seat> | <family> | <effort> | <variant> | <mode> | <reason> | reading <age>
```

The reason names the winning pool and the score. JSON rows keep the same keys.

Pools, the models they serve, cost tier, and how headroom is read (`usage.used`, `usage.reset`, `usage.window_s` or `usage.window_key`) live in `config/pools.json`. A pool with `"enabled": false` is not routed. Muse has a 5h pool and a weekly pool. Both read the fields `apply_muse` copies out of `muse-usage.json`. A missing reading is unknown. `--route lineup` drops a voter only when its pool is capped. A pool ahead of pace still votes, at low. Muse is dropped only when a Muse window is capped. Meta never chairs or builds, so the vendor rule does not drop it. `--route ask` returns two voters on two vendors, the ones with the most headroom. It leaves Muse out when either window is over pace. It does not drop a vendor for chairing. Each row has family, effort, and pace.

For each enabled pool and role the score is:

```
total = fit * headroom + lose_it - cost_weight * cost_tier - harness_penalty
```

`headroom` comes from the pace band (under, on, ahead, open, near cap). `lose_it` is the old burn nudge, applied to every pool that sets `lose_it`: the reset is inside `lose_it_hours` and used percent is at least `lose_it_gap` points below the elapsed percent. A non-empty `only` list pays `harness_penalty`, so a harness kept for one job is not the default spend. When every pool in a `cohort` is ahead, `cohort_ahead_factor` multiplies headroom again so a fallback outside the cohort can win. A cohort with a missing reading and nothing under or on pace asks, instead of skipping to that fallback.

`fit` blends three fields stored separately for each pool and role:

```
effective = (P * provider + R * prior + n * track) / (P + R + n)
```

`provider` is the owner's reading of the facts in config (cost tier, limits, window, context). `prior` is the external seed. `track` is the agreement rate from local outcome logs, and `n` is how many of those samples named that pool and role. `P` and `R` are `provider_pseudo` and `prior_pseudo` (both 8). A missing source is left out of the fraction, so it is not a zero. At `n = 0` the fit is the mean of the seeds that exist. At `n = 16` the track record weighs as much as those two seeds together. Larger `n` lets our own data outweigh the prior.

There is no fetcher. A weekly job may write `config/priors.json`, or the path in `SEAT_USAGE_PRIORS`, in the shape of `config/priors.example.json`. A number or `{"score", "samples", "source"}` overrides that pool's prior for that role. `samples` and `source` are notes for the job. The router uses `score` only.

Track records are JSON lines. Council's run log is `~/.local/state/seat-usage/runs.jsonl` (`SEAT_USAGE_RUNS`). A missing file is skipped. A run line has the task `role` and seat agreement in `seats`, `votes`, or `lineup` (chair, builder, voters). `agreed` or `agreed_with_verdict` is whether that seat matched the verdict. A seat is `seat` plus `family` (or `model`), or a `pool` id. A voter inherits the task role. `SEAT_USAGE_OUTCOMES` is an extra log of `{role, pool, agreed}` lines. `agreed` true counts as 1 and false as 0. The track score is the mean. A bad line is ignored.

A copied script with no `config/pools.json` beside it still prints usage. Routing then has no pools to score.

### Parity

Seat, family, mode, and effort match the old rule table on the cases the suite already named. The reason text is the score, which is the intentional difference. Two ask phrases got shorter. One untested corner does not match, and is called out at the bottom.

| Scenario | Old | New |
| --- | --- | --- |
| Fable under / on pace | Fable, suggest / default | same; reason cites the score |
| Fable ahead, weekly pool under | Opus on Max, suggest, effort `-` | same |
| Fable slice capped, Max open | Opus, suggest | same |
| Max weekly or 5h capped, Other Models open | Cursor Fable, suggest, xhigh | same |
| Other Models capped or unreadable | ask, default Fable seat | same ask phrases |
| Fable reading missing, Max open | default Fable, mode default | same |
| Codex under / on / ahead | Astra low, Astra low, Sol | same |
| Codex capped, including a full 5h window | ask, names the reopen | same |
| Codex or Fable in the lose-it band | burn, medium / max | same, for every such pool |
| Cursor Models under, or both Grok pools on | Cursor Grok, high, fast | same |
| Cursor on pace, SuperGrok under | SuperGrok | same |
| Cursor capped, SuperGrok open | SuperGrok | same |
| SuperGrok in the lose-it band | SuperGrok, burn, xhigh | same |
| Both Grok pools ahead, or both capped, Max open | Opus; chair high, builder low | same |
| SuperGrok reading missing, Cursor capped | ask | same phrase |
| Both Grok pools and Max weekly capped | ask | ask, phrase is now `Max weekly capped: ask Ryan` |
| Fable and the weekly pool both ahead | Opus (the ahead rule did not look at the weekly pace) | Fable keeps the seat. Its fit is higher and both bands match. Not covered by the old tests |

## Usage/routing seam

The seam was analyzed: it is clean and one-way (routing calls about 8 usage functions; usage does not call routing). No split for now; usage and routing stay in one repo.
