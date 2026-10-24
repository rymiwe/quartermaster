# quartermaster

Snapshot of Ryan's "router core": `seat-usage` (reads per-seat/pool usage for Claude, Grok, Cursor and related pools, with a cache) plus the `seat-route-hook` Claude Code hook that uses it to route Agent/Bash launches. Extracted as-is from his dotfiles. Not a polished or portable project.

`seat-usage` can update itself from GitHub. This is off by default; set `QUARTERMASTER_AUTO_UPDATE=1` to turn it on.

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
| `bin/seat-meters` | optional: draws the cached reading as a PNG laid out like orderly's meters panel (needs Pillow; no network). `seat-meters [--text] [out.png]`: a `Next host` line from `seat-usage --route host --cached --json`, one card per subscription with its cached renewal, orderly's model-family chips by orderly's rules (every family whose pools include the row, in orderly's family order; no plan or cap hides one; Google AI Pro's come from `google_ai_pro` in `config/pools.json`, as orderly gets them through `seat_rows`) and orderly's `$` on-demand chip on the Cursor and Grok Bot rows from `cursor_ondemand`, and orderly's Fable slice row on every Claude card: its percent, or `not on this plan` (`not on <plan>` with a plan name) with a muted title and an empty full-width track when the cache says the plan isn't Max (a plan name without `max`, or with no plan name, `claude_max` false) and the Claude read finished, else `unknown · seat-usage has no value`; orderly's ended plans (set not to renew, `<seat>_renews` false, with the paid-through stamp past) as a muted row with a full-width striped grey track and `sub ended` in place of usage, kept off the shared time scale and sunk below the active cards, a subscription's rows together; and a `55% · 5h 1% · resets Wed` stats line per pool, with orderly's red `caps 10d early` after the percent when the cache carries that pool's `<pool>_caps_early_s`; `--text` also prints the layout's words as JSON instead of the path. The cards run in orderly's order down two columns, cut where the columns come out most even, so the picture is no narrower than a 9:19.5 phone screen and a phone shows it at full width (a third column only if it would still be narrower); the type is orderly's at 1.7x so the smallest text is about 24 px when the picture is 1080 px wide. This is the only layout: `--phone`, which used to pick it over a wide one, is still accepted and does nothing. Cache path from `SEAT_USAGE_CACHE`. Google AI Pro is its own card |
| `bin/seat-usage-push` | optional: copies an allowlist of the cache to a secret gist for the phone widget, plus a numbers-only cache copy that `--pull` lands on a reader |
| `systemd/` | the `seat-usage-push` timer (every 5 minutes) and the `seat-usage-pull` timer for a pull-only reader |
| `android/` | Seats, the Android home-screen widget that reads that gist |

## Installed-path contract

The command name stays `seat-usage`; council hardcodes it. Install locations are unchanged:

- `~/.local/bin/seat-usage`
- `~/.claude/hooks/seat-route-hook`
- `~/.cache/seat-usage/latest.json`
- `~/.config/seat-usage/renewals.json` (real file is private; see `renewals.example.json`)
- `~/.local/state/seat-usage/asks.jsonl` and `history.jsonl`
- `~/.local/state/seat-usage/failures.jsonl` (`seat-usage --record-failure <seat> --reason credits|rate_limited|auth|max_turns|nonzero`; a line older than six hours does not exclude). Override with `SEAT_USAGE_FAILURES`.
- `~/.local/state/seat-usage/outcomes.jsonl` (optional extra track record; `SEAT_USAGE_OUTCOMES`)
- `~/.local/state/seat-usage/runs.jsonl` is council's run log. This command reads it and does not write it. Override with `SEAT_USAGE_RUNS`.

## Claude Code registration (owned by dotfiles)

This project only documents what it expects in `~/.claude/settings.json`; the file itself stays in dotfiles:

- `PreToolUse`, matcher `"Agent|Bash"`, running `~/.claude/hooks/seat-route-hook`, timeout 50
- `SessionStart`, running `seat-usage --cached`

## Status-line rows

Every cache write adds `seats` to `latest.json`: the groups and bars a status line draws (`seat_rows`). Each group has an `id` (`cl`, `cx`, `cu`, `gk`, `mu`, `gg`), a `hue`, and `bars`. A bar's `kind` is `seat`, `failed`, `empty`, or `off`. A new seat or state goes here, and the status line draws it with no change of its own. `~/.claude/statusline.py` (dotfiles) reads only these rows. It still owns the session part, staleness, and wording. A pulled copy carries numbers only, so a pull-only host has no `seats` and its status line draws no seat bars.

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

## renewals.json

`seat-usage` reads `~/.config/seat-usage/renewals.json` (`SEAT_USAGE_RENEWALS` overrides the path). Each top-level key is a seat name from `config/pools.json` (`claude`, `chatgpt`, `cursor`, `grokbot`, `grok`, `google`, `muse`). A missing file, or one that does not parse, changes nothing.

- `claude.at` and `claude.renews`: the hand-kept Claude term end (`YYYY-MM-DD`, or an ISO time) and whether it renews (`true`, `false`, or `null` for not known).
- `<seat>.ends` (optional, any seat): the last day the subscription can be used, as `YYYY-MM-DD` in this machine's time zone. Through that day nothing changes. From the next local midnight the seat is ended: every route (design, mixed, audit, review, a lineup with no kind, and ask) drops it, a skipped row has code `ended` and reason `ended <day>`, its pools show state `ended` in a route's `usage`, the Muse status line reads `muse | ended <day>`, its status-line bar is `off`, and it gets no burn line. An ended seat with no reading does not make a route ask. `null` or no `ends` means no end date. Any other value that is not a `YYYY-MM-DD` date stops the command with exit 2 and `seat-usage: <path>: <seat>.ends must be a YYYY-MM-DD date, got <value>` on stderr.

A canceled Muse plan that is paid through 2026-11-12 adds this key beside the ones already in the file:

    "muse": {"ends": "2026-11-12"}

On Windows, run the script from Git Bash. Command Prompt and PowerShell cannot run it.

## Update on use

Off by default. With `QUARTERMASTER_AUTO_UPDATE=1`, a run from a clean checkout of `main` fetches and fast-forwards at most once every 24 hours, then starts again so the new code runs. The timestamp is `~/.cache/quartermaster/seat-usage-update`, beside Conclave's `muse-usage.json`. The command prints `[seat-usage] updated` when the checkout moves.

The check does not stash, reset, or overwrite local changes. A dirty tree, a branch other than `main`, a detached HEAD, a missing network, or any git error skips it and prints nothing. `--route`, `--quiet`, `--cached`, `COUNCIL_VOTER=1`, and `CONCLAVE_VOTER=1` skip it. Without the opt-in, `seat-usage` never fetches. `QUARTERMASTER_NO_UPDATE=1` turns it off and wins over the opt-in. There is no background task.

## Google AI Pro (Antigravity)

`seat-usage` reads quota from the signed-in Antigravity CLI: `agy -p "/usage"`. The binary is `agy` on `PATH`, or `~/.local/bin/agy`. `SEAT_USAGE_AGY` overrides that path. The CLI prints one tab-separated row per window: pool, window label, percent remaining, reset time in UTC. The seat stores percent used (`100 - remaining`) for the Argon weekly window and for the Gemini and Claude/GPT weekly and 5-hour windows. Pace uses the same weekly and 5-hour windows as the other seats. A live check waits `SEAT_USAGE_MIN_INTERVAL_S` (default 10 minutes) before it runs `agy` again.

A missing `agy`, a session that is not signed in, a non-zero exit, a timeout, or output that does not parse is an error line. No percent is invented. Numbers already in the cache stay until a later reading replaces them. `--cached` does not run `agy`. `SEAT_USAGE_SKIP_GOOGLE=1` skips the live check, as do `COUNCIL_VOTER=1` and `CONCLAVE_VOTER=1`.

`seat-meters` draws those pools as their own Google AI Pro group, in a deep indigo blue (`#5468f0`) kept apart from SuperGrok's sky blue. Argon is the weekly row. Gemini and Claude/GPT each have a weekly bar and a 5-hour bar. There is no Antigravity API call.

## Phone widget (optional)

`bin/seat-usage-push` copies an allowlist of `~/.cache/seat-usage/latest.json` into a secret gist: percent, reset time, window, exhausted flag and reading time per seat, in the status line's pool order and hues. No token, cookie, plan text or `lines` leaves the machine. Anyone holding the gist id can read those numbers. It refreshes a cache whose reading (`ts`, or the file's mtime when there is no `ts`) is older than 10 minutes under the status line's lock, then pushes when the payload changed, and at least every 30 minutes as a heartbeat. When the status line holds the lock it waits up to 60 seconds (`flock -w`), then rechecks the age so a reading the status line just landed is not read again; `seat-usage` itself gets 90 seconds (`timeout`). Every run logs one `seat-usage-push: refresh ...` line to stderr, which lands in the journal (`journalctl --user -u seat-usage-push`): `ran` or `skip` with `why=` (`fresh`, `fresh-after-wait`, `lock-busy`, `lock-error`, `not-executable`, `lock-open-failed`, `error`), the reading's `age=` and `by=` (`ts`; `mtime` when there is no `ts`; `mtime(bad-ts)` when `ts` is more than 5 minutes in the future; `none` without a cache), the file's `mtime_age=`, the lock outcome and `wait=`, and for a run `seat-usage`'s `rc=` (124 is a timeout; 137 or -9, logged `killed`, is the SIGKILL 10 seconds after it), `dur=` and `new_age=`. Its stdout is discarded; only the last stderr line is kept, with anything token- or cookie-shaped replaced by `<redacted>`. `seats.json` also carries `age_s`, the reading's age in seconds when it was pushed (null without a `ts`).

`android/` is Seats, a home-screen widget that draws one row per seat in the status line's order. The phone never reaches the desktop: it reads the gist from `api.github.com` when Android's 30-minute update fires or when you tap it, and keeps the last good copy for when there is no network. `Seats.kt` holds the rules and is unit tested (`cd android && ./gradlew testDebugUnitTest`).

Desktop setup. `install.sh` does not install these; link them by hand:

    ln -s "$PWD/bin/seat-usage-push" ~/.local/bin/seat-usage-push
    gh auth refresh -s gist                   # the gh token needs the gist scope
    seat-usage-push --setup                   # creates the gist, prints its id
    systemctl --user link "$PWD/systemd/seat-usage-push.service" "$PWD/systemd/seat-usage-push.timer"
    systemctl --user enable --now seat-usage-push.timer
    seat-usage-push --dry-run                 # print the payload, push nothing

**Cap projection.** Each time it writes the cache, seat-usage projects every pool that has a used % and a reset time (the pools in `config/pools.json` plus the three Google AI Pro pools). Each pool is measured at its own reading's time (`as_of`), so a reading carried forward keeps its projection. The burn rate is linear from the window's start: used % divided by the time since the window opened (window length minus time left). At that rate the pool reaches 100% after `(100 - used) × elapsed / used` more seconds. If that comes before the reset, `<pool>_caps_early_s` (e.g. `claude_week_caps_early_s`, `google_gemini_5h_caps_early_s`) is the number of seconds early, rounded. No key means no projection. That covers: no reading, 0% used, already at 100% (the pool is capped, not projected), the pool reaching 100% at or after the reset, a reset that has passed by write time or was more than one window away at the reading, an unknown window, and the first 10% of a window (`CAPS_MIN_ELAPSED`), where one large job projects a cap that is not real. The projection is computed on the reading machine and travels in the push copy as plain numbers. A pulled copy shows them only once the gatherer runs a seat-usage that writes them.

One reader, one gatherer: the same gist also gets `latest.json`, a copy of the cache cut to an allowlist of numbers (percents, reset times, windows, ages, on-demand dollars, `as_of` stamps) and a few true/false flags (exhausted, renews, `claude_max`). No string is copied: no plan or status text, `lines`, `errors`, `burn`, or cookie flag. On the machine that does every reading (BLD) nothing changes beyond installing the timer above. On a reader with no vendor logins (the shared VM), put `{"id": "<gist id>"}` in `~/.config/seat-usage/gist.json` and run `seat-usage-push --pull`. It refilters, writes `~/.cache/seat-usage/latest.json` and Muse's `~/.cache/quartermaster/muse-usage.json`, and never runs `seat-usage`. `seat-meters` and `seat-usage --route ... --cached` then read that copy. `--pull` stamps the copy with `pulled_ts` (set after the filter, so a gist cannot supply it). On a copy with that stamp the configured bound is 20 minutes (`route_freshness_s.pulled`, 1200 seconds) instead of 10. That is the gatherer's 10-minute refresh plus one 5-minute push and one 5-minute pull, with no extra delay. It does not cover a healthy worst case. A floor-held Claude read can land almost 600 seconds after the previous stamp. That bumps the cache `ts`, so `seat-usage-push` (`STALE_S` 600) waits another 600 seconds. One push period and one pull period on top of that leave the reader seeing a stamp about 30 minutes old (`Fable reading 20m old`). This change does not widen `pulled`. About 1800 seconds would cover that stack. This box cannot read Surface's clock or the gatherer's rate-limit file, so it cannot confirm WSL sleep drift or an active 429 backoff. Both limits are `route_freshness_s` in `config/pools.json` (`{"live": 600, "pulled": 1200}`, in seconds); a missing or non-positive value falls back to those defaults. A live `seat-usage` run that merges into the cache drops `pulled_ts`, so live caches keep the 10-minute rule. `claude_max` is the Claude plan as one bit, set on the reading machine. A `weekly_scoped` (Fable slice) limit in the usage reading makes it true, since only Max has one. Otherwise the OAuth profile's `rate_limit_tier` (`claude_plan`) decides: a name with `max` is true, `claude_ai` (Pro) is false, and any other name, or a failed profile read, leaves it out (unknown). Routes read the flag first and the plan name only on an older cache. Off Max (`claude_max` false) no role ranks the Claude Fable slice, and the `bounded-question` role (the one whose primary is Fable) never seats Fable on Claude: it scores only the pools off the Claude seat that serve Fable (Other Models), as the council's Fable voter does. With the plan unknown the role keeps its hold default. The reader's meters show no plan name in the ChatGPT header and no error footer, because those are strings. `seat-usage-push --dry-run --cache` prints the copy.

### Reader host (pull-only) setup

A reader host (Surface, or the shared VM) does not read seats. It renders the copy BLD pushed. BLD keeps the push timer above. The reader needs four things:

1. The marker file, so this host never reads live. `SEAT_USAGE_PULL_ONLY=1` is the same switch for one process.

        mkdir -p ~/.config/seat-usage
        touch ~/.config/seat-usage/pull-only

2. The gist id, and a `gh` login that can read that secret gist. There is no unauthenticated fetch. The file is only the id:

        printf '%s\n' '{"id": "<gist id>"}' > ~/.config/seat-usage/gist.json

    Put the real id in that file on the machine. Do not commit it.

3. The pull timer, every 5 minutes, same cadence as the push. `install.sh` does not link these.

        ln -s "$PWD/bin/seat-usage-push" ~/.local/bin/seat-usage-push
        systemctl --user link "$PWD/systemd/seat-usage-pull.service" "$PWD/systemd/seat-usage-pull.timer"
        systemctl --user enable --now seat-usage-pull.timer

    The service runs `seat-usage-push --pull`. That refilters, writes `~/.cache/seat-usage/latest.json` with `pulled_ts`, and writes `~/.cache/quartermaster/muse-usage.json` when both Muse windows are in the copy, when `muse_exhausted` and `muse_exhausted_reset_ts` are, or when both are. A file that has only the exhausted flag has no used percent. It does not run `seat-usage`.

4. Verify, without printing the gist id:

        systemctl --user list-timers seat-usage-pull.timer
        seat-usage-push --pull
        test -f ~/.config/seat-usage/pull-only
        SEAT_USAGE_PULL_ONLY=1 seat-usage --route lineup --cached --kind mixed

    Stderr should say `pull-only cached`. A pulled copy has no `seats` rows, so the status line draws no seat bars. The numbers are the accounts BLD's CLIs are signed into.

Rollback: stop the timer and remove the marker. The cache file can stay.

    systemctl --user disable --now seat-usage-pull.timer
    rm ~/.config/seat-usage/pull-only

Without the marker, `seat-usage` reads live again. On a host with no vendor logins those reads fail.

Phone setup: install `seats.apk` from the `seats-latest` release, add the Seats widget, and paste the gist id or URL. CI (`.github/workflows/seats-widget.yml`) rebuilds that release on every push to `main` that touches `android/`.

Signing: this repo is public, so the key is not checked in. CI decodes it from the `SEATS_KEYSTORE_B64` secret, with its password in `SEATS_KEYSTORE_PASSWORD`, so each build installs over the last. Without those secrets (a fork, or a local build) the APK gets the Android debug key, and `main` publishes no release.

Tests: `python3 tests/test_seat_usage_push.py`.

## Known limits (shipped unfixed)

- Hardcoded paths (see contract above).
- WSL/Windows assumptions, including the `op.exe` WinGet path (`$SEAT_USAGE_OP` overrides).
- 1Password item path defaults to `op://seat-usage/grok web cookie/credential` (`$SEAT_USAGE_OP_REF` overrides).
- `op` authenticates with a per-machine service-account token read from `~/.config/seat-usage/op-token` (`$SEAT_USAGE_OP_TOKEN_FILE` overrides; keep it mode 0600 on the Linux filesystem, a looser mode prints a warning) and passed only in op's environment as `OP_SERVICE_ACCOUNT_TOKEN`. For `op.exe` under WSL the variable is also added to `WSLENV`. A token file wins over an `OP_SERVICE_ACCOUNT_TOKEN` already in the environment. With no file, or an empty one, op inherits the environment as before.
- A successful read is cached in `~/.cache/seat-usage/grok-cookie` (mode 0600) for 6 hours, and the stale copy is used when 1Password fails. The service account's daily read cap is shared by every machine, so live runs must not each read it.
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

`tests/test_update.py` runs the command in a temporary git checkout. It covers a fast-forward, the 24 hour stamp, a dirty tree, a non-main branch, a detached HEAD, diverged and ahead history, the opt-out, `--route`, `--quiet`, `--cached`, both voter variables, a failed fetch, a copy that is not a checkout, and the default with no opt-in. Run it with the same empty environment:

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

Pools, the models they serve, cost tier, and how headroom is read (`usage.used`, `usage.reset`, `usage.window_s` or `usage.window_key`) live in `config/pools.json`. A pool with `"enabled": false` is not routed. Muse has a 5h pool and a weekly pool. Google's Claude/GPT pools (`google_opus_5h`, `google_opus_week`) serve Opus through `agy --model claude-opus-5-5-<effort> -p "<brief>"` (`-p` takes the prompt as its value), from the fields `apply_google` stores. Their `only: ["agy"]` pays the harness penalty, and their builder and chair fit sits just under Claude's, so they win when the pools ahead of them are capped or behind pace. An unused weekly pool near its reset shows as a burn line. Their caps projection stays on the `google_claudegpt_*` keys. Wonder full is the frontier on that seat, the way Astra is on ChatGPT, and Wonder is the other model on it, the way Sol is. The weekly pool's family is Wonder full, and ahead of pace a scored route on it names Wonder. The voter stays Wonder full. Both read the fields `apply_muse` copies out of `muse-usage.json`. A missing reading is unknown, and the skip or ask reason is `muse: no usage source`. A Muse quota whose reset is still ahead caps both Muse windows with no used percent filled in. The printed Muse line then says `quota exhausted, resets <local time>`. Kind `mixed` skips a stale, unknown, or failed `read_state` seat and lists it (`skipped` in the text and in `--json`). It asks only when fewer than two eligible voters remain on two vendors outside the excluded vendor, or when the eligible voters share one vendor. Caps alone still leave the one-voter lineup. Design, audit, and a lineup with no kind still ask on the first unknown reading. `seat-usage --help` and `-h` print the usage text and exit before a reader, a lock, or a cache write. `--route lineup` with no `--kind` still excludes the chair's vendor, and the chair and the builder stay one vendor. `--kind design`, `mixed`, or `audit` excludes by what the ballot judges. Design excludes nobody. Mixed excludes `--author`, or the caller-passed `--builder-family` when `--author` is omitted. With neither, or with `--author human` or `--author none`, mixed excludes nobody: the builder the router fills in from pace is not the author. Audit excludes `--author` when it is named, and nobody when it is omitted. On those kinds, and on a lineup with no kind, Fable is required when the excluded vendor is xAI. A capped pool still drops a leg. A pool ahead of pace still votes, at low. Muse is dropped only when a Muse window is capped, or once its `renewals.json` `ends` day is over. Kind `review` is a separate roster, described below. It does not change design, mixed, audit, or a lineup with no kind. `--route ask` returns two voters on two vendors, the ones with the most headroom. It leaves Muse out when either window is over pace. It does not drop a vendor. Each row has family, effort, and pace.

For each enabled pool and role the score is:

```
total = fit * headroom + lose_it - cost_weight * cost_tier - harness_penalty
```

### Review council

`seat-usage --route review --builder-vendor VENDOR` seats one merge-gate reviewer. `seat-usage --route lineup --kind review` seats exactly two voters. Both use the same review roster, which is not `VOTER_ROSTER`: Fable (the Claude seat, or Cursor Other Models when that is the frontier the design voter already uses), Astra (`gpt-6-astra` on the Codex pool), Grok, Gemini Argon (the `google_argon_week` cache reading; Argon is not a routed pool, so design output is unchanged), and Muse wonder with contributor mode off. Spark models and Muse contributor mode are excluded. Vendor names are the config values `xai`, `anthropic`, `openai`, `google`, and `meta`. `grok` is an alias for `xai`.

The builder vendor is `--builder-vendor` when that flag is set. On a lineup it otherwise comes from `--author`'s family, and when `--author` is omitted it is the resolved builder's vendor (the same builder `--builder-family` already feeds). Neither chosen seat shares that vendor, and the two lineup voters are two vendor families.

Rank is the design pace order (under, then open, then on, then ahead, then near cap), then lower used percent, then roster order. The headroom number is the scoring table for that pace (under 1.00, on 0.90, open 0.95, ahead 0.55, near cap 0.22), times the cohort factor when the pool has one. Human output and `--json` include those pace and headroom numbers, the used percent, and every skipped seat with a reason. A seat that loses the rank is skipped as `lower pace and headroom rank`.

Argon uses the same weekly pace bands as a pool. At 95 percent used it is near cap. A reset that has already passed is unknown, including a window that reads 100 percent. An unknown reading, including one older than the route freshness window, and a capped seat are ineligible and listed as skipped. They do not ask by themselves. The route asks only when fewer than the required number of eligible seats remain (one for `--route review`, two for `--kind review`). A stale Fable reading therefore drops Fable and still seats the next two eligible seats. Design still asks on that stale reading. Mixed skips it and asks only when two vendors do not remain.

Every settled review pick appends one JSONL line. The default path is `~/.local/state/seat-usage/review-picks.jsonl`. `SEAT_USAGE_REVIEW_LOG` overrides it. The line has `ts`, `id`, `repo`, `pr`, `ballot`, `builder_vendor`, the chosen reviewers with pace and headroom, `why`, and `findings` (each `{id, reviewer, text, grade}` with `grade` blank until a grader records one). Pass `--repo`, `--pr`, and `--ballot` on the route to store them. Picks are logged ungraded. `--review-findings --pick latest|ID --reviewer LABEL --file PATH` attaches that reviewer's findings and stores `grade` blank. A `grade` in the attached file is dropped.

`--blind-findings --pick latest|ID [--out FILE]` prints the findings as `Reviewer A`, `Reviewer B`, and so on, and writes that same text to `--out` when the flag is set. Model, vendor, seat, and family names are redacted, including the reviewer label and every string on the pick's `reviewers`. The letter-to-reviewer map is stored in a sidecar beside the log (`review-picks.blindmap.json`, the log name with that suffix). The map is not in the printed text or in `--out`.

`--grade-findings --pick latest|ID --grader ryan|blind-grader --grade f1=real --grade f2=false` records grades and does not print the map. `SEAT_USAGE_GRADER` supplies the grader when `--grader` is omitted. Each graded finding gets `grader` (`ryan` or `blind-grader`), `graded_at` (UTC), and `grade_status` `graded`. The command exits nonzero and writes nothing when the process is a chair. Chair launchers set `SEAT_USAGE_ROLE=chair`, `CONCLAVE_CHAIR=1`, or `COUNCIL_CHAIR=1` in the chair process before it starts. A grader process is separate: it does not inherit those, and it sets `--grader blind-grader` or `SEAT_USAGE_GRADER=blind-grader`. Conclave does not set a chair marker today. `CONCLAVE_CHAIR_MODEL` is published for the whole ballot, including voters, so it is not treated as one. The recommended conclave change, not made here, is to export `CONCLAVE_CHAIR=1` on the chair process only.

`--unblind --pick latest|ID` prints the letter-to-model and vendor map only after every finding on that pick has `grade_status` `graded` from `ryan` or `blind-grader`. Otherwise it exits nonzero and prints no map.

`--mark-self-graded` copies the log to `/workspace/conclave/tmp/council/blind-grading/backup/review-picks.jsonl.<UTC timestamp>`, checks the copy with cmp and sha256, and only then rewrites. A finding with a non-empty `grade` and no allowed-grader provenance keeps that value on `chair_grade_suspect`, sets `grade` blank, and sets `grade_status` to `suspect-self-graded`. No row or finding is removed. `SEAT_USAGE_REVIEW_BACKUP_DIR` overrides the backup directory.

The router does not read this log. `graded_findings_for_learning(rows)` is the only sanctioned read for later steering. It returns findings with `grade_status` `graded` from `ryan` or `blind-grader`, and it drops ungraded and suspect findings. Steering from grades is not implemented. A later change may steer only after at least 20 graded runs per model on comparable PR kinds (the same kind, and a diff in the same size band), and only when every compared model has those 20 grades and the real rates differ by at least 15 percentage points. Until that threshold exists in code, grades do not change who is seated.

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
