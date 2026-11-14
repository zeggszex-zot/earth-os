"""Claude Code, built into the shell.

This package is what lets a coding agent control CDOS and change its own
interface without becoming a way around the rules the rest of the tree keeps.
It is rung 2 of ``docs/SELF_MODIFICATION.md`` made real, with the gate the
document specified:

* :mod:`.policy`    — what the agent may never touch, and what it may run
  without a press. Human-write-only, like the catalogue.
* :mod:`.workspace` — the editable git tree the agent works in.
* :mod:`.gate`      — the verification gate. The suite is the arbiter; a
  change that fails it is set aside, not argued with.
* :mod:`.session`   — runs ``claude`` headless and streams what it does.
* :mod:`.bridge`    — the running shell's side of the control channel. Every
  effectful act goes through :meth:`Shell.run_capability`, the gated path.
* :mod:`.mcp_server` — the agent's side: a small stdio MCP server that turns
  tool calls into bridge requests.

Nothing here imports GTK, so all of it runs under the test suite.
"""
