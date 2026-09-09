# OpenMonitor Implementation Plan

> Execute tasks against the approved DESIGN.md. Use test-first development and
> independently review lifecycle behavior before publication.

**Goal:** Detached command watchers that notify the same Codex thread without model polling.
**Architecture:** Python CLI launches a detached supervisor; private atomic state and
advisory locks track ownership; Codex queue is the delivery boundary.
**Tech stack:** Python 3.11+, standard library runtime, unittest, GitHub Actions.
**Spec:** ../../../DESIGN.md (approved 2026-09-09).

## Constraints

macOS/Linux only. Explicit UUID target. No implicit shell. Bounded logs and messages.
No automatic retries after ambiguous submission. No sandbox bypass. No model calls while quiet.

## Tasks

- [x] Implement state and supervisor (`openmonitor/state.py`, `supervisor.py`) and
  CLI (`cli.py`, `__main__.py`). Start with black-box tests in `tests/test_cli.py`:
  `start --mode exit -- python -c 'print("done")'` must detach, preserve bytes,
  and submit once to a fake Codex executable. Run `python -m unittest discover -s tests -v`
  before implementation to establish the expected failure, then after each feature.
- [x] Add regressions using real child processes for nonzero exit, missing executable,
  stderr, UTF-8 split writes, flood limits, quiet streams, duplicate names, stop,
  timeout, failed delivery, retry and grandchildren cleanup. A fake queue command
  isolates only the external submission boundary; all process lifecycle logic is real.
- [x] Create README, MIT license, `skills/openmonitor/SKILL.md`, packaging and CI.
  Instructions must distinguish queued from processed and preserve authorization boundaries.
- [x] Independently review concurrency, cancellation and private state handling.
  Resolve findings and run full regression suite; validate installed CLI in a clean venv.
- [x] Run isolated App Server integration and real-model wakeup with explicit thread
  identity. Prove first turn ends before watcher completion; no inference while quiet;
  next turn is triggered by queued event. Record only sanitized results in docs.
- [x] Inspect exact staged files, publish public repo, create implementation PR,
  require green CI before merge, and read back remote default branch and visibility.

Published through PR #1; all four platform/Python checks passed before merge.
