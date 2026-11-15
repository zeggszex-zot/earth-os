# Claude Code, built into CDOS

*A coding agent that can drive the shell and change its interface — inside
the rules, not around them.*

The **CLAUDE CODE** screen (main column, `F8`, or say *"Claude"*) runs
[Claude Code](https://docs.claude.com/en/docs/claude-code) headless against
CDOS's own source. Ask it to do something with the computer, or to change the
interface, and it does — with three things between it and the machine:

| What it wants | What stands in the way |
|---|---|
| change a file in the interface | the **gate**: the full suite must pass, or the change is set aside |
| change the machine (a capability) | the shell's **gated path**: effectful ones wait for CONFIRM |
| run any other command | the **permission prompt**: shown on the LCARS strip, waits for ALLOW |

It is rung 2 of [SELF_MODIFICATION.md](SELF_MODIFICATION.md), made executable.

## Using it

```
F8                                   open the CLAUDE CODE screen
"Computer, Claude, make the main screen buttons larger"    ask by voice
"Computer, Claude"                   just open the screen
```

Type a request and press Enter. The transcript shows what Claude reads, edits
and runs as it happens (`▸ EDIT earth_os/screens/main.py`). When the turn
ends, the gate judges it:

```
GATE · ACCEPTED · 1 file · 486 tests pass · commit 41486796
Press RELOAD INTERFACE to run the new code.
```

| Button | Does |
|---|---|
| NEW SESSION | forget the conversation (the workspace and its history are untouched) |
| INTERRUPT | stop the current request (`Ctrl+C` too); the gate still judges what it left |
| HISTORY | the workspace's commits, and anything set aside in `git stash` |
| UNDO LAST CHANGE | revert the last *gate* commit — asks first |
| RELOAD INTERFACE | restart into the new code — asks first; lit only when there is something to load |

The conversation survives a reload: it resumes by session id.

### Setup

```sh
npm install -g @anthropic-ai/claude-code
claude                  # once, in a terminal, to sign in (or set ANTHROPIC_API_KEY)
earth-os --doctor     # "claude code ... ok"
```

The gate needs `/usr/bin/python3`, the system interpreter (AGENTS.md: a green run on the wrong
interpreter is worse than a red one). Without it nothing can be verified, so
nothing is kept.

## Where the changes go

Claude works in a **workspace** — a git working tree of the full source, tests
included. In order:

1. `[claude] workspace` in `config.toml`, if set;
2. the checkout CDOS is running from, if it is a git tree (`python3 -m earth_os`);
3. `~/.local/share/earth-os/src`, created on first request from the template
   `install.sh` puts in `$PREFIX/share/earth-os/source`.

The installed launcher runs from (3) whenever it exists, which is what makes an
installed CDOS self-modifying. If that tree ever fails to import, the launcher
says so on stderr and runs the stock install instead. To go back to stock for
good, delete the directory; `EARTH_OS_STOCK=1 earth-os` skips it once.

A reinstall always wins. `install.sh` stamps each build (`BUILD`, a hash of
the code), and a workspace made from a different build is moved aside to
`src.before-<date>` on the next launch — history intact — and the installed
version runs. Before this, the first request froze the machine on that day's
code and every later upgrade appeared to do nothing. `earth-os --version`
prints which tree is running.

The workspace must be clean before a request: the gate judges *everything*
that differs from where the turn began, and it must never set aside work that
was not Claude's. Commit or stash your own edits first.

## The gate

`earth_os/agent/gate.py`, after every turn:

1. nothing human-write-only was touched → `policy.PROTECTED`
2. `platform/` and `capability_corpus.py` were not both changed → a new
   capability is rung 3, and a person grants those
3. `/usr/bin/python3 -m unittest discover -s tests -q` passes — which renders
   every screen at four resolutions, so green means the interface still draws
4. the suite did not shrink to get there
5. **one** commit, with the verification output in its message

Any failure: the change goes to `git stash` named for the reason. Commits Claude
made mid-turn are rewound and judged with the rest; Claude is denied `git
commit`, `reset`, `stash` and friends outright, because the gate is the only
thing that commits.

### Human-write-only

The built-in agent can never change these, however green the suite:

```
earth_os/platform/capabilities.py    the catalogue and the gate
tests/test_boundary.py                 the ratchet
tests/test_capabilities.py             the gate's own tests
AGENTS.md                              the rules
earth_os/agent/policy.py             this list, and what runs unasked
earth_os/agent/gate.py               the arbiter
tests/test_agent.py                    what holds the agent to both
```

Enforced twice: Claude Code's own deny rules, and the gate, which rejects the
change before the suite runs.

## Controlling the shell

Claude Code gets a small MCP server (`earth_os/agent/mcp_server.py`, stdlib
only) that forwards to a Unix socket inside the running shell
(`earth_os/agent/bridge.py`, at `$XDG_RUNTIME_DIR/earth-os/agent.sock`,
mode 0600):

| Tool | |
|---|---|
| `status`, `screens` | what is on screen, palette, language, pending presses, recent conversation |
| `go`, `say` | switch screens; speak as the computer |
| `palettes`, `set_palette` | the live colour scheme |
| `screenshot` | render a screen of the *running* shell; the image comes back to Claude |
| `capabilities`, `invoke` | the closed catalogue, run through `Shell.run_capability` |
| `approve` | Claude Code's permission prompt, answered by a person on the strip |

To see its *edits* before they are loaded, Claude renders the workspace
headlessly with `tools/screenshot.py` and reads the PNG — that is on the
allow-list, as is running the suite.

**Nothing on the bridge is a second door.** It never calls
`capabilities.invoke`; an effectful capability goes on the confirmation strip
and the tool call waits for the person. CANCEL, DENY and silence (10 minutes)
all mean nothing ran. A request arriving while you are on another screen brings
you to CLAUDE CODE and plays the computer chirp.

## What runs without a press

`earth_os/agent/policy.py` — `ALLOWED_TOOLS`: reading and searching, editing
inside the workspace (the gate judges every edit), the MCP tools above, read-only
git (`status`, `diff`, `log`, `show`), `ls`, the suite, and the screenshot and
gallery tools. Everything else Claude Code wants to run appears on the strip as
`CLAUDE CODE REQUESTS · BASH — <command>` with ALLOW / DENY.

## Configuration

```toml
[claude]
enabled     = true
command     = "claude"               # path or name of the Claude Code CLI
model       = ""                     # blank = Claude Code's default
workspace   = ""                     # see "Where the changes go"
python      = "/usr/bin/python3"     # the gate's interpreter
auto_verify = true
```

## Why this shape

The alternative — Claude Code with `--dangerously-skip-permissions` in the
source tree — would work, and would make every rule in AGENTS.md advisory. The
point of this codebase is that its rules are checked, not requested; an agent
built into it is held to them the same way, by the same suite, with the same
press for anything that changes the machine.
