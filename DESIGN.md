# OpenMonitor: proposed public v1

Status: design for review; implementation and live end-to-end validation pending.
Research date: 2026-09-09. Local tools: Codex CLI 0.153.4, Claude Code 2.1.266.

## Outcome

Let an agent start a local command, end its turn, and receive an event in the
same conversation when the command finishes or emits a selected line. Waiting
must cause no model calls. The watcher may itself poll an external service;
that is different from repeatedly invoking the model to check it.

Recommended implementation: a small Python CLI (standard library at runtime),
a Codex skill, and a detached local supervisor. Use the installed `codex queue`
command for delivery. Publish as `dp-pcs/openmonitor`, with an MIT license,
macOS/Linux support, regression tests and CI. Windows native process-tree
management is deferred, not implicitly supported.

## What Claude actually exposes

Claude's Monitor is a stream of events, not simply an exit callback. An agent
starts a watcher command; stdout lines become notifications in the conversation.
Current documentation also supports WebSocket sources and ties monitor cleanup
to session/subagent shutdown. Plugin monitors have unique names to avoid duplicate
processes and can start on plugin activation or first skill invocation.

Sources:

- [Tools reference](https://code.claude.com/docs/en/tools-reference#monitor-tool)
- [Plugin monitors](https://code.claude.com/docs/en/plugins-reference#monitors)
- [Scheduling limitations](https://code.claude.com/docs/en/scheduled-tasks#limitations)

The reusable design is to perform detection and filtering in ordinary code and
invoke the model only for actionable events. A shell command is a flexible source
adapter, while stable task identity and a stop operation make ownership explicit.

These are documented external contracts. Claude Code's internal scheduler is not
public source in the material inspected. We have not reverse engineered it or
independently reproduced its reported bugs. Do not describe this research as proof
of its exact internal implementation, or describe open reports as confirmed causes.

## Lessons with evidence

Anthropic's [public changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
confirms these fixes:

| Version | Confirmed change | OpenMonitor requirement |
| --- | --- | --- |
| 2.1.257 | Print mode exited with an armed monitor; stopped subagents left monitors alive; proactive sessions polled instead of idling | Supervisor lifetime independent of the model turn; explicit ownership and expiry; no model heartbeat |
| 2.1.252 | Large background failure notifications could exceed API request limits | Bound event payloads and retained logs separately |
| 2.1.247 | Container restarts could silently lose background work | Persist terminal/delivery state and expose interrupted work |
| 2.1.221 | Silent watcher exit had an ambiguous description | Always record exit code, signal and empty-output status |
| 2.1.207 | Plugin configuration substitution into shell commands permitted injection | Execute argument arrays; require an explicit shell for shell syntax |

Open reports provide additional test scenarios, not independently verified findings:

- [#80610](https://github.com/anthropics/claude-code/issues/80610): stderr-only
  failures were invisible to the model. Capture both streams, preserve their
  identities, and deliver bounded diagnostic events in line mode.
- [#83384](https://github.com/anthropics/claude-code/issues/83384): reader stalls,
  queues failing to catch up, and orphaned pipelines. Expose byte/event counts,
  last activity, pending delivery and stop reason; drain pending batches without
  requiring another output line; terminate the owned process group.
- [#86085](https://github.com/anthropics/claude-code/issues/86085): agent completion
  confused with an idle agent owning a live watch. Report process and delivery
  states separately; target the persistent parent thread in v1.
- [#87519](https://github.com/anthropics/claude-code/issues/87519): allegedly
  fabricated notifications. Cause is unverified. Store original output bytes,
  stable event IDs and timestamps; never have a model summarize source events
  before delivery. Events are evidence to inspect, not authorization to act.

## Codex delivery: verified source behavior

The installed CLI supports `codex queue --thread UUID --message TEXT`. Its
[0.153.4 implementation](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/tui/src/session_queue_commands.rs)
uses `thread/queue/add`, generates a new client message UUID on every invocation,
and reports the returned queued-submission ID. It refuses unsupported servers
and certain overrides that would bypass a running daemon.

The [queue service](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/ext/queue/src/service.rs)
persists an item, then wakes a loaded idle thread. Busy threads defer dispatch
until idle. Interrupted threads are not automatically woken. A cold thread can
retain a message but must be resumed before it can run. Its
[upstream integration tests](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server/tests/suite/v2/thread_queue.rs)
cover these boundaries and the 100-submission queue capacity. We inspected these
tests; we have not run them locally.

Consequences:

- "Accepted by Codex" is not "agent processed it." Name the state `queued`.
- Never silently resume a cold thread, unarchive it, or override an interruption.
- Require an explicit thread UUID; do not select the latest session or resolve
  an ambiguous name. Support an explicit remote endpoint if needed.
- Queue text is user input at the transport level. Prefix it clearly as an
  automated event, with no new human instruction or approval. Treat captured
  output as untrusted data and require source verification before consequential
  actions. This labeling mitigates confusion; it is not a security boundary.
- A crashed delivery command might already have submitted its message. V1 cannot
  promise exactly-once delivery. Persist an `uncertain` outcome and do not retry
  it automatically. An operator can explicitly retry, accepting duplication.

The [App Server tool-output interface](https://learn.chatgpt.com/docs/app-server#start-a-turn)
is an alternative for a future custom client. A custom client would own the
conversation lifecycle; a Codex fork would own core wake logic. Both add
maintenance and installation burden that the queue-based v1 avoids.

## Proposed command and state contract

```text
openmonitor doctor
openmonitor start --thread UUID --name build --mode exit -- make test
openmonitor start --thread UUID --name errors --mode lines -- tail -F app.log
openmonitor list
openmonitor status MONITOR_ID
openmonitor stop MONITOR_ID
openmonitor retry MONITOR_ID --accept-duplicate-risk
```

Store private state outside the repository with owner-only directory/file
permissions. A random monitor ID identifies immutable launch configuration,
bounded stdout/stderr logs, events and delivery attempts. An exclusive lock
prevents duplicate ownership. Starting the same active name/thread combination
must fail rather than spawn another watcher.

The detached supervisor owns the command and process group. It reads both pipes
without blocking one behind the other, handles partial UTF-8 and final lines
without newlines, batches event bursts, and enforces output, event-count and
lifetime limits. Line mode flushes queued data after a short debounce even if
the producer becomes silent. Overflow is explicit, never silently discarded.
Exit mode sends one bounded completion event for both success and failure.

No implicit shell interpolation. Shell pipelines remain available via an explicit
`sh -c '...'` argument. Commands run with the supervisor's OS permissions; this
wrapper does not inherit or implement Codex's sandbox policy. The skill must
never use it to bypass a denied shell command.

Process state: `starting`, `running`, `exited`, `cancelled`, `timed_out`, `failed`.
Delivery state: `pending`, `sending`, `queued`, `uncertain`, `cancelled`.
Cancellation suppresses notifications not yet submitted; it cannot retract a
message already accepted by Codex. Stopping records a reason and terminates the
owned process group. A supervisor crash must remain distinguishable from a
healthy silent watcher; status checks must not blindly signal a saved PID that
could have been reused.

V1 intentionally has explicit independent lifetime with a finite expiry. Ending
a model turn does not stop a watcher. Closing the UI does not guarantee teardown;
the user must stop it or let it expire. Do not advertise Claude-style automatic
session ownership cleanup without a reliable session lifecycle subscription.

## Validation required before calling v1 working

1. Real subprocess tests: success, nonzero exit, missing executable, stderr-only
   failure, partial line, UTF-8 boundaries, output flood, simultaneous streams.
2. Lifecycle tests: duplicate start, cancellation during execution/delivery,
   timeout, process-group teardown including grandchildren, dead supervisor.
3. Delivery tests: zero invocations while quiet, one completion submission,
   burst coalescing, draining a quiet backlog, unsupported Codex, failed/ambiguous
   delivery, explicit retry, bounded message size.
4. A real Codex session: originating turn ends before an externally triggered
   command completes; queue submission starts a new turn in that same thread.
   Repeat with a busy thread and verify orderly deferred handling. Validate
   interrupted and cold-thread behavior without claiming a wake occurred.
5. Publish only synthetic fixtures and sanitized verification results. Keep
   session transcripts, local paths, private logs, credentials and `.remember/`
   out of Git. Include a license, CI, installation instructions, explicit support
   limits, and the evidence above.

## Review decision

Approve the CLI + Codex skill design, or choose the custom-client/native-fork
alternative before implementation. No public repository has been created yet.
