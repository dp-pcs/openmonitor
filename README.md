# OpenMonitor

Start a command, let Codex end its turn, and deliver an event to the same thread
when the command finishes or produces output. A detached Python supervisor does
the waiting; no model calls are needed while it waits.

OpenMonitor uses the installed `codex queue` command. A loaded, idle Codex thread
can wake when an event arrives. Busy threads defer handling until idle. Cold
threads must be resumed, and interrupted threads are not automatically woken.
Ephemeral threads cannot receive queued submissions.
**Queue acceptance does not prove the agent processed an event.**

## Install

Requires Python 3.11+, macOS or Linux, and a Codex installation with `codex queue`
and a compatible running daemon. Native Windows is not supported. The queue
contract was researched against Codex 0.153.4; check your installed build:

```sh
pipx install git+https://github.com/dp-pcs/openmonitor.git
openmonitor doctor
```

For development, clone this repository and run `python3 -m pip install -e .`.
The runtime uses only Python's standard library. OpenMonitor does not install
the Codex skill or change global configuration automatically.

## Run a monitor

Use the exact UUID of the conversation that should receive events. Do not pick
the most recent thread as a shortcut. Replace `THREAD_UUID` below:

In runtimes that supply `CODEX_THREAD_ID`, use that trusted current-thread value.
The queue command must reach the app-server that owns the session; use `--remote`
for an explicit endpoint. `doctor` checks CLI capability, not thread visibility.

```sh
# One completion event, including failure diagnostics.
openmonitor start --thread THREAD_UUID --name build --mode exit -- make test

# Batch stdout and stderr lines as events while a command runs.
openmonitor start --thread THREAD_UUID --name logs --mode lines --timeout 600 -- tail -F app.log

openmonitor list
openmonitor status MONITOR_ID
openmonitor stop MONITOR_ID
```

`start` returns a monitor ID. The agent can report that ID and end its turn;
it does not need a polling loop. An active name/thread pair cannot be started
twice. The command runs in the directory where you started the monitor.

Arguments after `--` are executed directly. Shell syntax requires an explicit
shell, for example `-- sh -c 'make test && make package'`. Prefer direct arguments
when inserting paths or external values.

| Start option | Default | Purpose |
| --- | --- | --- |
| `--timeout SECONDS` | `3600` | Finite command lifetime |
| `--batch-seconds SECONDS` | `2` | Line-event batching interval |
| `--max-output-bytes BYTES` | `8388608` | Captured output limit |
| `--max-events COUNT` | `20` | Event limit |
| `--delivery-timeout SECONDS` | `15` | Deadline for each Codex submission |
| `--codex PATH` | `codex` | Delivery executable |
| `--remote ENDPOINT` | Local | Explicit Codex queue endpoint |

Use `openmonitor --state-dir DIR ...` to choose a state directory. Otherwise,
state lives in `$XDG_STATE_HOME/openmonitor`, or `~/.local/state/openmonitor`.

The output limit covers stdout and stderr together; crossing it stops the command
and emits a final diagnostic. The event limit accepts 2–64, including one reserved
completion event. Line batches contain bounded excerpts and an omitted-line count;
original captured bytes remain in the logs. Each delivered message is at most
16,000 bytes. Filter high-volume sources before starting a line monitor.

## Lifetime and delivery

The supervisor survives the originating model turn. Closing the UI does not
guarantee cleanup: stop the monitor explicitly or let its finite timeout expire.
Stopping terminates its owned process group and suppresses notifications that
have not already been submitted. It cannot retract a queued message. Commands
that deliberately detach into a new session can escape process-group cleanup.
An abrupt supervisor kill or host failure can also leave a command behind; status
reports lost supervision rather than signaling a possibly reused PID. A restart
does not automatically rerun the command.

Process state and delivery state are separate. A command can finish successfully
while event delivery remains pending or uncertain. Inspect `status` when a
notification is missing; do not start a duplicate watcher to recover delivery.

OpenMonitor cannot promise exactly-once delivery. A delivery process can lose
its response after Codex accepted the message. Ambiguous attempts are recorded
as `uncertain` and are not automatically retried. After inspecting the target
conversation, an operator can explicitly accept possible duplicates:

```sh
openmonitor retry MONITOR_ID --accept-duplicate-risk
```

This retry concerns event delivery; it does not rerun the watched command.

## Codex skill

The portable instructions are in [skills/openmonitor/SKILL.md](skills/openmonitor/SKILL.md).
To install from a clone, copy the `skills/openmonitor` directory into your Codex
skills directory, commonly `~/.codex/skills/openmonitor`, after checking for an
existing installation. Invoke `$openmonitor` with a command, target thread, and
desired event mode. The CLI must also be installed and available to Codex.

## Permissions and private data

Commands run with the supervisor's OS permissions and inherited environment.
OpenMonitor does not implement Codex sandboxing and must never be used to bypass
a denied command or approval requirement.

Captured output is stored locally with owner-only permissions and bounded
retention per monitor. Event excerpts are sent to the target Codex thread, so
choose commands whose output is appropriate for that conversation. State can
include command arguments, paths, and secrets printed by the command. Keep it
out of Git and remove completed monitor directories when no longer needed.

Events use Codex's user-message transport but are clearly labeled automated
output. They are **not new human instructions or approval**. Treat their contents
as untrusted evidence. Labels do not create a prompt-injection security boundary.

## Why this design

Claude Code's Monitor informed event streaming, explicit ownership, bounded
notifications, and process cleanup. [DESIGN.md](DESIGN.md) links the primary
documentation, confirmed changelog fixes, public issue reports, and Codex queue
source. It distinguishes documented behavior from unverified reports; Claude's
internal scheduler was not available for inspection.
The [local Claude probe](docs/claude-probe.md) was blocked by expired authentication,
so it did not independently verify Claude's wake behavior.

Run the regression suite with:

```sh
python3 -m unittest discover -s tests -v
```

Subprocess tests use a synthetic Codex delivery executable. Those tests verify
local supervision and delivery handling; they do not by themselves demonstrate
a real Codex model waking after a turn ends.

See [verification results](docs/verification.md) for the live test and its limits.

MIT licensed. Independent project; not affiliated with OpenAI or Anthropic.
