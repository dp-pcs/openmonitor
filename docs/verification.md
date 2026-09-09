# Verification

## Live Codex wakeup

On September 9, 2026, the opt-in fixture passed against **codex-cli 0.153.4** on macOS ARM64, with the locally configured direct OpenAI provider and `gpt-6-astra` model. No global configuration or existing daemon was changed.

The fixture starts its own loopback WebSocket app-server, initializes the experimental API, and creates an isolated durable thread in a temporary directory with read-only sandboxing and approval policy `never`. It uses the existing Codex login, so running it makes real model requests and may consume your subscription quota or API credit.

Observed sequence:

1. An initial real model turn reached `turn/completed` with status `completed`.
2. OpenMonitor launched a detached command and returned to its caller. The command waited for a gate file.
3. For a 1.5-second quiet window, no new turn started.
4. The external fixture released the gate. The command printed a known token and exited successfully.
5. OpenMonitor invoked the installed `codex queue` against that isolated server.
6. The **same thread** emitted a new `turn/started`, then `turn/completed` with status `completed`. Its assistant message acknowledged the fixture token.
7. OpenMonitor's saved state recorded process `exited`, return code `0`, and delivery `queued`.

The WebSocket observer waited for lifecycle events; the agent did not repeatedly call tools to poll the command. Observing a successful second model turn is stronger evidence than a queue command returning zero. The product still reports only `queued`: it does not subscribe to that observer or claim processing acknowledgements.

### Reproduce

Requires Node.js with native WebSocket support (tested with Node 26), Python 3, an installed compatible Codex CLI, and a working Codex login:

```sh
node scripts/verify-live.mjs --run --busy
```

The `--busy` flag adds two model turns to check deferred delivery; omit it for the two-turn idle test. Without `--run`, the script prints usage and performs no live work. Optional `CODEX_BINARY` and `PYTHON` environment variables select executables. It inherits your model/provider; it does not override them. Run from a trusted checkout. This test is deliberately excluded from ordinary CI.

The fixture requests stop for every monitor, waits up to eight seconds for supervisors to stop, then removes temporary state and stops its own server. If shutdown is not confirmed, it preserves state and exits nonzero. Codex may retain the synthetic durable thread in its normal history. It prints sanitized boolean results rather than thread UUIDs, transcripts, private paths, or authentication material.

### Busy-thread delivery

The same live run also passed the optional busy-thread scenario. While a real model turn was producing bounded fixture output, another OpenMonitor command completed and its durable delivery receipt became `queued` **before the active turn completed**. The observer then saw the active turn complete, followed by a distinct wake turn on the same thread. That wake turn completed and acknowledged the output token.

This directly verifies deferred delivery at the turn boundary for the tested runtime. The test reports `inconclusive` if the model finishes too quickly for the queue receipt to be observed while active; it never treats that timing race as a busy-path pass.

### Observed rejection and unverified boundaries

An initial attempt using an **ephemeral thread** was rejected by Codex `thread/queue/add` with JSON-RPC code `-32600`: `ephemeral thread does not support queued submissions`. OpenMonitor saved `uncertain`, and no wakeup was reported. The passing fixture therefore uses a durable thread.

This live result proves idle wakeup for a loaded durable thread on the tested server/version. It does **not** establish behavior for interrupted turns, unloaded threads, app-server restart, client disconnect, remote authentication, or recovery after machine reboot. Source-based descriptions of those paths are not live verification. Claude's behavior and reported fixes are investigated separately; this fixture does not reproduce Claude internals.

The fixture uses a gate file rather than a timing guess to separate command completion from the already-completed initial turn. The quiet-window duration is a test observation, not a delivery latency guarantee.
