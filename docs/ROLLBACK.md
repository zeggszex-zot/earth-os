# Going back

Three ways, from the smallest step to the largest. Each was carried out
before this was written (see [TESTING.md](TESTING.md), "Rollback").

Nothing here deletes what the computer has remembered. Where your data is, and
how to remove it if you want it gone, is at the end.

## 1. Switch Earth Mind off (no reinstall)

In `~/.config/earth-os/config.toml`:

```toml
[mind]
terminal = false     # the terminal alone goes back to 1.0: translator and shell only
```

```toml
[mind]
enabled = false      # no Earth Mind anywhere: the Companion Engine answers on its own
```

```toml
[mind]
enabled = false
[companion]
enabled = false      # and no Companion: the voice path's tier 4 is 1.0's model path, exactly
```

Restart Earth OS. With both off, nothing in `earth_os/mind/` or
`earth_os/companion/` is imported. What remains of 2.0 is the World Model's
ledger ("remember that…"; `[world] enabled = false` switches that off too),
the title cap (`V2.0`), three extra rows in `earth-os --doctor`, and that
uninstalling no longer deletes your data.

In the terminal, TRANSLATOR OFF has the same effect for that screen until it
is switched back on, and `!command` always runs a line exactly as typed.

## 2. Reinstall 1.0 over 2.0

The archive is a git repository, and the state Earth OS 1.0.0 arrived in is
tagged.

```sh
cd earth-os
./install.sh --user --uninstall          # or: sudo ./install.sh --uninstall
git stash -u                             # only if you have changed anything
git checkout earth-os-1.0.0-memory3
./install.sh --user                      # or: sudo ./install.sh
earth-os --doctor                        # first line reads: earth-os 1.0.0
```

Uninstall with the **2.0** installer, as above, before checking out the tag:
1.0's uninstaller removes the whole of `~/.local/share/earth-os` for a
`--user` install, which is also where your data is. 2.0's removes only what it
installed.

1.0 reads a configuration file that has 2.0's `[mind]`, `[companion]`,
`[computer]` and `[world]` sections in it and ignores them.

To come forward again: `git checkout main && ./install.sh --user`. Everything
remembered is where it was.

## 3. Undo one step of the work

The history is small on purpose, one commit per step, newest first:

```sh
git log --oneline earth-os-1.0.0-memory3..main
```

| Tag or commit | What it is |
|---|---|
| `earth-os-2.0.0` | This release |
| *Earth Mind tests, and what writing them found* | The tests, four fixes, conversation memory, the installer shipping CDOS Computer |
| *Unified terminal…* | The terminal goes through Earth Mind (`[mind] terminal = false` is the switch for this one) |
| *Earth Mind: one coordination layer…* | The Companion, World Model and computer link ported; `earth_os/mind/` added |
| *Import CDOS Computer unchanged…* | `cdos-computer/` added; nothing else touched |
| `earth-os-1.0.0-memory3` | Earth OS 1.0.0 exactly as it arrived, including the uncommitted work that was in the archive |

`git revert <commit>` undoes one of them and keeps the history; run
`/usr/bin/python3 -m unittest discover -s tests -q` afterwards.

## What 2.0 leaves on the machine

| Path | What | Used by 1.0? |
|---|---|---|
| `~/.config/earth-os/config.toml` | Your settings, with 2.0's sections added | Yes; the new sections are ignored |
| `~/.local/share/earth-os/mind/memory.db` | Earth Mind's memory: system diagnoses, game worlds, characters, kept conversations | No |
| `~/.local/share/earth-os/mind/worlds/` | Game worlds you installed | No |
| `~/.config/earth-os/personalities/` | Personalities you wrote | No |
| `~/.local/share/earth-os/world.json` | What you asked the computer to remember | No |
| `~/.local/share/earth-os/computer/` | The embedded CDOS Computer's log | No |
| `~/.local/share/earth-os/semantics.json` | Learned meanings | Yes |

To remove everything Earth Mind remembered, with Earth OS closed:

```sh
rm -r ~/.local/share/earth-os/mind
```

To remove only what *you* asked it to remember, say "forget everything about
me", or delete `world.json`.
