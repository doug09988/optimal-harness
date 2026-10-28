"""Path confinement and fail-closed, file-scoped Linux command sandbox."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import resource
import shutil
import subprocess
import tempfile
import time
from typing import Any


class PolicyError(RuntimeError):
    pass


def normalized(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise PolicyError("invalid path")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("", ".", "..") for part in path.split("/")):
        raise PolicyError("absolute, empty, dot and parent components are forbidden")
    if p.parts[0] in (".harness", ".git"):
        raise PolicyError("internal paths are forbidden")
    return str(p)


def confined(root: Path, path: str) -> Path:
    """Reject symlinks throughout the path, including the final component."""
    path = normalized(path)
    root = root.resolve(strict=True)
    current = root
    for part in PurePosixPath(path).parts:
        current = current / part
        if current.is_symlink():
            raise PolicyError(f"symlink path denied: {path}")
    if not current.resolve(strict=False).is_relative_to(root):
        raise PolicyError("path escaped root")
    return current


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class FileBroker:
    def __init__(self, root: Path, reads: list[str], writes: list[str], max_bytes: int = 131072):
        self.root = root.resolve(strict=True)
        self.reads = set(map(normalized, reads)) | set(map(normalized, writes))
        self.writes = set(map(normalized, writes))
        self.max_bytes = max_bytes

    def read(self, path: str, offset: int = 0, length: int | None = None) -> str:
        path = normalized(path)
        if path not in self.reads and not any(p.endswith('/**') and path.startswith(p[:-2]) for p in self.reads):
            raise PolicyError("read not on declared allowlist")
        full = confined(self.root, path)
        length = self.max_bytes if length is None else length
        if type(offset) is not int or type(length) is not int or offset < 0 or not 0 < length <= self.max_bytes:
            raise PolicyError("invalid byte page")
        if not full.is_file():
            raise PolicyError("file missing or non-regular")
        with full.open('rb') as f:
            f.seek(offset)
            data = f.read(length)
        # Never invent replacement characters in a verbatim evidence read.
        import codecs
        try:
            decoder = codecs.getincrementaldecoder('utf-8')('strict')
            text = decoder.decode(data, final=(offset + len(data) >= full.stat().st_size))
        except UnicodeDecodeError as exc:
            raise PolicyError('read offset is not UTF-8 aligned or file is not UTF-8') from exc
        if data and not text:
            raise PolicyError('page too short for UTF-8 character; increase length')
        return text

    def write(self, path: str, data: str, append: bool = False) -> int:
        path = normalized(path)
        if path not in self.writes and not any(p.endswith('/**') and path.startswith(p[:-2]) for p in self.writes):
            raise PolicyError("write not on declared allowlist")
        if not isinstance(data, str):
            raise PolicyError("write data must be text")
        full = confined(self.root, path)
        if full.exists() and not full.is_file():
            raise PolicyError("not a regular file")
        old = full.read_bytes() if append and full.exists() else b""
        result = old + data.encode("utf-8")
        if len(result) > self.max_bytes:
            raise PolicyError("write exceeds file limit")
        atomic_write(full, result)
        return len(result)


def _limits(max_memory_mb: int, cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_mb * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def linux_sandbox_command(root: Path, command: dict[str, Any], max_seconds: int = 30,
                    trusted_files: dict[str, Path] | None = None) -> dict[str, Any]:
    """Run a predeclared argv with ONLY mounted project files.

    Denies execution if bubblewrap or user namespaces are unavailable. Worker
    input never supplies argv, environment or mount points.
    """
    if os.name != "posix" or not shutil.which("bwrap"):
        raise PolicyError("Linux bubblewrap required for command execution")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(s, str) and s for s in argv):
        raise PolicyError("command argv must be fixed nonempty strings")
    read_paths = [normalized(p) for p in command.get("reads", [])]
    write_paths = [normalized(p) for p in command.get("writes", [])]
    trusted_files = {normalized(k): v.resolve(strict=True) for k, v in (trusted_files or {}).items()}
    if set(read_paths) & set(write_paths):
        raise PolicyError("a sandbox mount cannot be both read and write")
    if set(trusted_files) & set(read_paths + write_paths):
        raise PolicyError("trusted verifier mount shadows project path")
    root = root.resolve(strict=True)
    mounts = read_paths + write_paths
    for rel in mounts:
        full = confined(root, rel)
        if not full.is_file():
            raise PolicyError(f"command mount missing: {rel}")
    for rel, full in trusted_files.items():
        if not full.is_file() or full.is_symlink():
            raise PolicyError(f"private verifier source invalid: {rel}")

    args = ["bwrap", "--die-with-parent", "--unshare-all", "--new-session"]
    for system_dir in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(system_dir).exists():
            args += ["--ro-bind", system_dir, system_dir]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/job"]
    parents = set()
    for rel in mounts + list(trusted_files):
        p = PurePosixPath(rel).parent
        while str(p) != ".":
            parents.add(str(p))
            p = p.parent
    for p in sorted(parents, key=lambda s: (s.count("/"), s)):
        args += ["--dir", "/job/" + p]
    for rel in read_paths:
        args += ["--ro-bind", str(confined(root, rel)), "/job/" + rel]
    for rel in write_paths:
        args += ["--bind", str(confined(root, rel)), "/job/" + rel]
    for rel, full in trusted_files.items():
        args += ["--ro-bind", str(full), "/job/" + rel]
    args += [
        "--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONPATH", "/job",
        "--chdir", "/job", "--", *argv,
    ]
    timeout = min(max_seconds, int(command.get("timeout_seconds", max_seconds)))
    if timeout <= 0:
        raise PolicyError("invalid command timeout")
    started = time.monotonic()
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            preexec_fn=lambda: _limits(int(command.get("memory_mb", 512)), timeout + 2),
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "reason": "command_timeout", "seconds": round(time.monotonic()-started, 3), "stdout": "", "stderr": ""}
    output = {
        "exit_code": result.returncode,
        "reason": "exited" if result.returncode >= 0 else "killed_by_signal",
        "seconds": round(time.monotonic()-started, 3),
        "stdout": result.stdout, "stderr": result.stderr,
    }
    if result.returncode and ("bwrap:" in result.stderr or "Operation not permitted" in result.stderr):
        raise PolicyError("sandbox could not establish isolation: " + result.stderr[:300])
    return output


def sandbox_command(root, command, max_seconds=30, trusted_files=None):
    from .commands import run_command
    return run_command(root, command, max_seconds, trusted_files)
