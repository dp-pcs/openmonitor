# OpenMonitor

Local command watchers that wake Codex through `codex queue`.
Python 3.11+, standard library runtime; macOS and Linux.

- `openmonitor/`: CLI, private state, process supervision and delivery.
- `tests/`: real-process regression tests with a synthetic Codex delivery boundary.
- `skills/openmonitor/`: portable Codex instructions.
- `DESIGN.md`: research, delivery guarantees and limitations.

Run `python3 -m unittest discover -s tests -v`.
Never claim queue acceptance proves model processing. Keep private monitor data,
credentials and live session transcripts out of Git. Use argument arrays, bounded
output and explicit cancellation. Do not signal a process using only a saved PID.
