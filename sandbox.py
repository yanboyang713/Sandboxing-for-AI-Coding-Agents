#!/usr/bin/env python3
"""
sandbox.py — Docker-based sandbox manager/runtime for AI coding agents.

Features
- Allowlist/denylist command policy enforcement
- Ephemeral, restricted Docker containers (non-root, no-new-privileges, caps dropped)
- Read-only root FS with RW bind-mounted workspace
- Optional network isolation
- CPU / memory / PID limits with robust fallbacks if host cgroups are missing
- JSONL audit logging
- Simple file-level snapshot/rollback transactions
"""

import os
import re
import json
import time
import uuid
import tarfile
import shutil
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any

import docker
from docker.errors import APIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _mkdirp(p: str) -> None:
    os.makedirs(p, exist_ok=True)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #

@dataclass
class SandboxPolicy:
    """
    Execution policy for the sandbox.
    - allow: list of allowed base executables
    - deny_patterns: regex patterns that must not appear in the command string
    - env_allowlist: environment variables allowed to pass into the container
    - working_subdir: optional subdirectory inside /app to set as working directory
    """
    allow: List[str] = field(default_factory=lambda: ["python", "python3", "bash", "sh", "cat", "echo"])
    deny_patterns: List[str] = field(default_factory=lambda: [
        r"rm\s+-rf\s+/",                              # destructive root wipe
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};\s*:",    # classic fork bomb
    ])
    env_allowlist: List[str] = field(default_factory=lambda: ["PYTHONUNBUFFERED"])
    working_subdir: str = ""

    @staticmethod
    def from_yaml(path: str) -> "SandboxPolicy":
        import yaml  # lazy import
        with open(path, "r") as f:
            obj = yaml.safe_load(f) or {}
        return SandboxPolicy(
            allow=obj.get("allow", []),
            deny_patterns=obj.get("deny_patterns", []),
            env_allowlist=obj.get("env_allowlist", []),
            working_subdir=(obj.get("working_subdir", "") or ""),
        )


class PolicyError(Exception):
    """Raised when a command violates the sandbox policy."""
    pass


# --------------------------------------------------------------------------- #
# Docker Sandbox
# --------------------------------------------------------------------------- #

class DockerSandbox:
    """
    Restricted ephemeral container runner.

    Security hardening:
    - Non-root user (1000:1000)
    - Drop all capabilities
    - security_opt: no-new-privileges
    - Read-only root filesystem
    - tmpfs mounts for /tmp and /run
    - Optional network disable
    - CPU/memory/PIDs limits (with automatic fallbacks if the host lacks controllers)

    Notes:
    - The project workspace is bind-mounted RW under /app (or /app/<subdir>).
    - Each `run` creates a new container and removes it after completion.
    """

    def __init__(
        self,
        image: str,
        workspace: str,
        policy: SandboxPolicy,
        cpus: float = 1.0,
        mem: str = "512m",
        pids_limit: int = 128,
        network: bool = False,
        log_dir: str = "./logs",
    ):
        self.image = image
        self.workspace = os.path.abspath(workspace)
        self.policy = policy
        self.cpus = cpus
        self.mem = mem
        self.pids_limit = pids_limit
        self.network = network
        self.client = docker.from_env()

        _mkdirp(self.workspace)
        _mkdirp(log_dir)
        self.audit_path = os.path.join(log_dir, "audit.jsonl")

    # ---------- Paths & Mounts ----------

    def _mount_dest(self) -> str:
        """
        Normalize the bind mount target:
        - "", ".", "./"  => "/app"
        - "foo/bar"      => "/app/foo/bar"
        """
        sub = (self.policy.working_subdir or "").strip()
        if sub in ("", ".", "./"):
            return "/app"
        return "/app/" + sub.strip("/")

    def work_mount(self) -> Dict[str, Dict[str, str]]:
        """Return the docker-py volumes mapping for the workspace bind-mount."""
        return {self.workspace: {"bind": self._mount_dest(), "mode": "rw"}}

    # ---------- Policy ----------

    def _check_policy(self, cmd: str) -> None:
        """
        Enforce allowlist/denylist against the command string.
        We check the base executable (basename of the first token) against allowlist,
        and scan the entire command string against deny regexes.
        """
        stripped = cmd.strip()
        if not stripped:
            raise PolicyError("Empty command not allowed.")

        base = stripped.split()[0]
        exe = os.path.basename(base)

        if self.policy.allow and exe not in self.policy.allow:
            raise PolicyError(f"Command '{exe}' not in allowlist: {self.policy.allow}")

        for pat in self.policy.deny_patterns:
            if re.search(pat, stripped):
                raise PolicyError(f"Command matches denied pattern: {pat}")

    def _whitelist_env(self) -> Dict[str, str]:
        return {k: v for k, v in os.environ.items() if k in self.policy.env_allowlist}

    # ---------- Audit ----------

    def _audit(self, event: Dict[str, Any]) -> None:
        event["ts"] = time.time()
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # ---------- Container Creation ----------

    def _create_container(self, cmd: str, limits: Dict[str, bool]):
        """
        Create (but do not auto-remove) the container with the requested limits.
        """
        kwargs = dict(
            image=self.image,
            command=["bash", "-lc", cmd],
            working_dir=self._mount_dest(),
            volumes=self.work_mount(),
            environment=self._whitelist_env(),
            network_disabled=(not self.network),
            user="1000:1000",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,
            tmpfs={"/tmp": "", "/run": ""},
            detach=True,              # keep detached; we'll manage lifecycle
            stderr=True,
            stdout=True,
            # DO NOT set remove/auto_remove here; we remove explicitly later.
        )
        # Apply limits if enabled
        if limits.get("cpu", True):
            kwargs["nano_cpus"] = int(self.cpus * 1e9)  # e.g., 1.0 CPU
        if limits.get("mem", True) and self.mem:
            kwargs["mem_limit"] = self.mem
        if limits.get("pids", True) and self.pids_limit:
            kwargs["pids_limit"] = self.pids_limit
        return self.client.containers.run(**kwargs)

    # ---------- Execution ----------

    def run(self, cmd: str, timeout: int = 10) -> Dict[str, Any]:
        """
        Run a single command inside a locked-down container.

        Returns:
            dict with keys:
              - ok (bool)
              - code (int)         [present when ok is True or process exited]
              - stdout (str)
              - stderr (str)
              - error (str)        [present when a Docker/other error occurs]
        """
        self._check_policy(cmd)

        event = {
            "id": str(uuid.uuid4()),
            "cmd": cmd,
            "timeout": timeout,
            "image": self.image,
            "mount_dest": self._mount_dest(),
        }

        # Start with all limits on; selectively disable if the host lacks controllers.
        limits = {"cpu": True, "mem": True, "pids": True}
        fallbacks = []

        try:
            for _ in range(4):  # try with fallbacks
                container = None
                try:
                    container = self._create_container(cmd, limits)

                    # Wait for completion
                    try:
                        res = container.wait(timeout=timeout)
                    except Exception as e:
                        # Collect whatever logs exist, then clean up
                        out = ""
                        err = f"wait_error: {e}"
                        try:
                            out = container.logs(stdout=True, stderr=False).decode("utf-8", "ignore")
                            err_logs = container.logs(stdout=False, stderr=True).decode("utf-8", "ignore")
                            err = (err + "\n" + err_logs).strip()
                        finally:
                            try:
                                container.remove(force=True)
                            except Exception:
                                pass
                        result = {"ok": False, "error": err}
                        self._audit({**event, **result})
                        return result

                    # Gather logs before removing
                    out = container.logs(stdout=True, stderr=False).decode("utf-8", "ignore")
                    err = container.logs(stdout=False, stderr=True).decode("utf-8", "ignore")
                    code = res.get("StatusCode", 1)
                    ok = (code == 0)
                    result = {"ok": ok, "code": code, "stdout": out, "stderr": err}
                    self._audit({**event, **result})
                    return result

                except APIError as e:
                    # Inspect daemon explanation and toggle limits if it's a cgroup issue
                    exp = (getattr(e, "explanation", "") or str(e)).lower()
                    changed = False
                    if ("pids.max" in exp or ("pids" in exp and "no such file" in exp) or "pids limit" in exp) and limits["pids"]:
                        limits["pids"] = False; fallbacks.append("pids"); changed = True
                    elif ("memory.max" in exp or ("memory" in exp and "no such file" in exp) or "memory cgroup" in exp) and limits["mem"]:
                        limits["mem"] = False; fallbacks.append("memory"); changed = True
                    elif ("cpu.max" in exp or "cfs_quota" in exp or ("cpu" in exp and "no such file" in exp)) and limits["cpu"]:
                        limits["cpu"] = False; fallbacks.append("cpu"); changed = True

                    if changed:
                        event["limits_fallback"] = ",".join(fallbacks)
                        continue  # retry with reduced limits

                    # Not a known cgroup issue → surface it
                    msg = f"docker.APIError: {getattr(e, 'explanation', None) or str(e)}"
                    result = {"ok": False, "error": msg}
                    self._audit({**event, **result})
                    return result

                finally:
                    # Explicit cleanup if container was created
                    if container is not None:
                        try:
                            container.remove(force=True)
                        except Exception:
                            pass

            # Exhausted retries
            result = {"ok": False, "error": "Unable to create container after limit fallbacks"}
            self._audit({**event, **result})
            return result

        except Exception as e:
            result = {"ok": False, "error": repr(e)}
            self._audit({**event, **result})
            return result

    def run_sequence(self, cmds: List[str], timeout: int = 10) -> Dict[str, Any]:
        """
        Run a sequence of commands; stop at first failure.

        Returns:
            {"ok": True} on success,
            or {"ok": False, "failing_cmd": <cmd>, "result": <run result>} on failure.
        """
        for c in cmds:
            r = self.run(c, timeout=timeout)
            if not r.get("ok", False):
                return {"ok": False, "failing_cmd": c, "result": r}
        return {"ok": True}


# --------------------------------------------------------------------------- #
# Transaction (host-side workspace snapshot / rollback)
# --------------------------------------------------------------------------- #

class Transaction:
    """
    Simple file-level snapshot/rollback on the host workspace.

    - __enter__: create a tar snapshot of the workspace
    - __exit__:
        - on success: remove the snapshot
        - on exception: restore the snapshot (rollback) and swallow the exception
          so callers (e.g., agents) can retry
    """

    def __init__(self, sandbox: DockerSandbox):
        self.sandbox = sandbox
        _mkdirp("/tmp/snapshots")
        self.snap = f"/tmp/snapshots/{uuid.uuid4()}.tar"

    def __enter__(self):
        # Create a snapshot of the current workspace
        with tarfile.open(self.snap, "w") as tar:
            tar.add(self.sandbox.workspace, arcname="workspace")
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            # Success: cleanup snapshot
            try:
                os.remove(self.snap)
            except Exception:
                pass
            return False  # no exception to suppress
        else:
            # Failure: rollback workspace from snapshot
            tmp_dir = f"/tmp/restore-{uuid.uuid4()}"
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                with tarfile.open(self.snap, "r") as tar:
                    tar.extractall(tmp_dir)
                src = os.path.join(tmp_dir, "workspace")

                # Wipe current workspace contents
                for name in os.listdir(self.sandbox.workspace):
                    p = os.path.join(self.sandbox.workspace, name)
                    try:
                        if os.path.isdir(p) and not os.path.islink(p):
                            shutil.rmtree(p)
                        else:
                            os.remove(p)
                    except FileNotFoundError:
                        pass

                # Restore from snapshot
                for name in os.listdir(src):
                    s = os.path.join(src, name)
                    d = os.path.join(self.sandbox.workspace, name)
                    if os.path.isdir(s) and not os.path.islink(s):
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                try:
                    os.remove(self.snap)
                except Exception:
                    pass

            logging.warning("Rolled back workspace due to error: %s", exc)
            # Swallow the original exception to allow the caller to retry
            return True

