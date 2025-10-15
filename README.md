# Fault-Tolerant Sandboxing for AI Coding Agents

> Minimal, single-file OpenAI agent + Docker sandbox with transactional rollback and audit logging.

**Status:** Alpha (working quicksort demo, sandbox manager; service API & CI image next)
**Date:** Oct 14, 2025

---

## ✨ Executive Summary

Fault-tolerant Docker sandbox and a minimal OpenAI-powered agent that generates, runs, and tests **quicksort** code.
The agent executes end-to-end inside a **restricted container** with **audit logging** and **automatic rollback**.

---

## 📁 What’s in this repo

Top-level artifacts:

* `agent_quicksort.py` — Single-file agent using the OpenAI **Responses API** to draft `quicksort.py`, run tests in the sandbox, and print a demo.
* `sandbox.py` — Docker sandbox runtime with:

  * allow/deny **policy checks**
  * **read-only** root FS; **tmpfs** for `/tmp` and `/run`
  * **no-network** mode
  * JSONL **audit logs**
  * file-level **snapshot/rollback**
  * **robust fallbacks** for missing cgroup controllers (**PIDs/Memory/CPU**)
* `policy.yaml` — Allow/deny policy (e.g., allow `python`, `bash`, `echo`; deny destructive patterns), plus an **env allowlist**.
* `Dockerfile.sandbox` — Base sandbox image (non-root, `uv` installed, `/app` as workdir).
* `workdir/`, `tests/`, `logs/` — Bind-mounted workspace, unit tests, and audit trails.

---

## ✅ What already works

* **End-to-end generate & test loop**
  `uv run agent_quicksort.py` creates `quicksort.py`, executes tests inside the sandbox, and reports pass/fail.
  Validated this interactively with: `✅ Quicksort generated and tested successfully.`

* **Safety measures**

  * Allowlist/denylist policy gate on each command
  * Read-only root FS; writable bind-mounted workspace (**/app**) only
  * `tmpfs` for `/tmp` and `/run`
  * Optional network isolation
  * JSONL audit logs and **transactional snapshot/rollback** of the workspace

---

## 🚀 How to run (current demo)

```bash
# 1) Build the sandbox image (adjust tag to match your Dockerfile.sandbox)
docker build -f Dockerfile.sandbox -t ai-sandbox:py312 .

# 2) Set runtime vars
export SANDBOX_IMAGE=ai-sandbox:py312
export OPENAI_API_KEY=****                 # your key
export OPENAI_MODEL=gpt-5-nano             # known-good with this agent
# optional: network off by default; policy/env from policy.yaml

# 3) Sync deps (without installing the project as a package)
uv sync --no-install-project

# 4) Run the agent (creates quicksort.py, runs tests in container)
uv run agent_quicksort.py
```

---

## 🔧 Configuration

Environment variables the sandbox/agent respects:

| Variable                | Purpose                                                                              | Default                                 |
| ----------------------- | ------------------------------------------------------------------------------------ | --------------------------------------- |
| `OPENAI_API_KEY`        | OpenAI API key                                                                       | (required)                              |
| `OPENAI_MODEL`          | Model name for the agent                                                             | `gpt-4.1-mini` (overridden in examples) |
| `SANDBOX_IMAGE`         | Docker image used for sandbox runs                                                   | `ai-sandbox:py312`                      |
| `PYTHONUNBUFFERED`      | In env allowlist by default                                                          | `1` (recommended)                       |

---

## 🗺️ Roadmap / Milestones

**M1 — Manager as a service**

* `manager/service.py` (FastAPI) exposing `/run`, `/audit`, `/snapshot`, `/restore`
* `compose.yaml` for `sandbox-manager` + Docker socket (least privilege)

**M2 — Reproducible CI path**

* CI Dockerfile: COPY `env/pyproject.toml` + `uv.lock` → `uv sync --locked` → tests
* Push image to GHCR/local registry; run the **same** image locally & in CI

---

