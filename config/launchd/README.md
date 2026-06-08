# launchd 24/7 scheduling for quant_data (v0.5 §4.4 / §8 Week 2)

This directory holds the macOS `launchd` template that drives the
`quant_data` evening sync at 17:30 on weekdays (Mon-Fri).

We do **not** auto-load it. The template is delivered as-is; you decide
when to install it. Two paths are supported:

## Path A — launchd plist (preferred for 24/7, survives reboot)

The shipped template: `com.quant.data.sync.plist`.

```bash
# 1. Copy the template to your user LaunchAgents (launchd picks it up from here)
cp config/launchd/com.quant.data.sync.plist ~/Library/LaunchAgents/

# 2. Edit it and inject the TUSHARE_TOKEN (do NOT commit the result).
#    Either replace the __FILL_ME_FROM_KEYCHAIN__ placeholder with the value
#    from `security find-generic-password -s tushare -w`, OR (cleaner) load
#    the token at agent start via `security`:
#       <key>ProgramArguments</key>
#       <array>
#           <string>/bin/sh</string>
#           <string>-c</string>
#           <string>exec /Users/allenwang/Code/quant-meta-team/.venv/bin/python \
#               -m quant_data.cli run-once --lookback 1</string>
#       </array>
#    and put the keychain lookup in a wrapper script.

# 3. Validate (launchd prints the parsed plist)
plutil -lint ~/Library/LaunchAgents/com.quant.data.sync.plist

# 4. Load it (will NOT run on load — RunAtLoad=false)
launchctl load ~/Library/LaunchAgents/com.quant.data.sync.plist

# 5. Force a manual run to validate the wiring
launchctl kickstart -k gui/$(id -u)/com.quant.data.sync

# 6. Tail the logs
tail -f ~/Code/quant-meta-team/logs/launchd.out.log \
        ~/Code/quant-meta-team/logs/launchd.err.log

# 7. Inspect the next scheduled fire time
launchctl list | grep com.quant.data.sync

# 8. To stop + remove:
launchctl unload ~/Library/LaunchAgents/com.quant.data.sync.plist
rm ~/Library/LaunchAgents/com.quant.data.sync.plist
```

### Failure / observability

- `launchd` writes its own log to `~/Library/Logs/launchd.*` — any non-zero
  exit from `run-once` lands there.
- Our `run-once` CLI exits 2 when any of the 5 tables fails (see
  `quant_data/cli.py::cmd_run_once`).
- For a deeper view, run `make report` after a sync — it prints row counts,
  the cursor table, and disk usage.

## Path B — in-process APScheduler (`make run-scheduler`)

If you don't want launchd / `~/Library/LaunchAgents`, run the scheduler
in-process. Same cron expression, same 5-table sweep, no plist editing.

```bash
# Foreground (block on a 17:30 weekday cron)
make run-scheduler

# Dry-run (validates the trigger is wired without hitting tushare)
.venv/bin/python -m quant_data.cli serve-scheduler --dry-run
```

Caveats vs. launchd:

- Does **not** survive reboot / logout. The process must be re-launched.
- One Python process holds the APScheduler + the tushare rate-limit bucket.
- Best for a long-running dev box or `tmux`/`screen` session.

## Why both?

| Need | Use |
|------|-----|
| Server / always-on mac, 24/7 | launchd plist (Path A) |
| Local dev box, manual control | `make run-scheduler` (Path B) |
| Manual one-off catch-up | `make run-once` / `make run-once-dry-run` |

Both share the **same** 5-table sweep code in `quant_data.scheduler.run_once`,
so resume / cursor / lineage semantics are identical.
