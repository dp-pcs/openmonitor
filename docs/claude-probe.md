# Claude Monitor live compatibility probe

Date: 2026-09-09. CLI: Claude Code 2.1.266 on macOS.

**Result: Monitor was advertised, but authentication prevented execution.**
This probe does not establish that a monitor can wake this installation after
the first model turn ends.

We inspected `claude --help` and launched one print-mode session in a new private
temporary directory. The only fixture was a shell script that waited 12 seconds
and printed `OPENMONITOR_SYNTHETIC_EVENT`. Only the built-in `Monitor` tool was
enabled and allowed. The request asked for `FIRST_RESULT` immediately after
arming the monitor and `EVENT_RECEIVED` if a later notification arrived.

The invocation used `--print --verbose --output-format stream-json --safe-mode
--no-session-persistence --strict-mcp-config --tools Monitor --allowedTools
Monitor --max-turns 4`. A separate supervisor imposed a 75-second process limit.
No provider, model, credentials, global configuration, or permission-bypass
settings were changed. Safe mode disabled customizations and retained normal
authentication behavior.

Observed stream:

| Observation | Result |
| --- | --- |
| Initial tool inventory | Included `Monitor` |
| Tool-use blocks | None |
| Assistant/result error | `Failed to authenticate: OAuth session expired and could not be refreshed` |
| Result fields | `subtype: success`, `is_error: true` |
| Process exit code | `1` |
| Total elapsed wall time | Approximately 0.69 seconds |

The result subtype alone would have misclassified this attempt: the explicit
error field and process exit code identify the failure. No watcher was launched,
so there are no first-result or notification timestamps to compare. We did not
retry another authentication route or inspect credentials. Raw session output
remains outside the repository; this document contains only sanitized findings.

Claude's documented behavior and confirmed changelog fixes remain useful design
evidence in [DESIGN.md](../DESIGN.md). This unsuccessful live probe does not verify
their implementation or reproduce the reported lifecycle bugs.
