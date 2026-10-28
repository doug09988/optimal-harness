"""Coordinator-pinned command broker. macOS is a detection-only boundary.

A staged per-file cwd jail prevents accidental access; it is NOT OS isolation.
Code can use absolute paths/network on macOS. requires_isolation fails closed.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from .policy import PolicyError, confined, normalized, atomic_write


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(root, command, max_seconds=30, trusted_files=None):
    root = Path(root).resolve(strict=True)
    argv = command.get('argv')
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or '\x00' in x for x in argv):
        raise PolicyError('command requires fixed argv')
    executable = shutil.which(argv[0])
    if executable is None:
        raise PolicyError('command executable unavailable')
    if command.get('executable_sha256') and sha(Path(executable).resolve()) != command['executable_sha256']:
        raise PolicyError('command executable pin changed')
    isolated = sys.platform.startswith('linux') and shutil.which('bwrap') is not None
    if command.get('requires_isolation', False) and not isolated:
        raise PolicyError('command requires_isolation: OS sandbox unavailable')
    reads = [normalized(p) for p in command.get('reads', [])]
    writes = [normalized(p) for p in command.get('writes', [])]
    if set(reads) & set(writes) or any(p.endswith('/**') for p in reads + writes):
        raise PolicyError('commands require disjoint exact file mounts')
    trusted_files = trusted_files or {}
    if set(trusted_files) & set(reads + writes):
        raise PolicyError('private mount shadows worker file')
    before = {p: sha(confined(root, p)) for p in reads}
    private_before = {p: sha(Path(f)) for p, f in trusted_files.items()}
    timeout = min(float(max_seconds), float(command.get('timeout_seconds', max_seconds)))
    if timeout <= 0:
        raise PolicyError('command deadline exhausted')
    if isolated:
        from .policy import linux_sandbox_command
        result = linux_sandbox_command(root, command, int(timeout), trusted_files)
        result['containment'] = 'bubblewrap'
        return result
    work = root / '.harness' / 'commands'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work, prefix='cmd-') as tmp:
        jail = Path(tmp)
        for p in reads + writes:
            dest = confined(jail, p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = confined(root, p)
            if src.exists():
                if not src.is_file():
                    raise PolicyError('nonregular mount')
                shutil.copyfile(src, dest)
            elif p in reads:
                raise PolicyError('missing read mount')
        for p, source in trusted_files.items():
            dest = confined(jail, p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        temp = jail / '.tmp'
        temp.mkdir()
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(jail),
               'TMPDIR': str(temp), 'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONPATH': str(jail), 'LANG': 'en_US.UTF-8'}
        started = time.monotonic()
        outpath, errpath = jail / '.stdout', jail / '.stderr'
        with outpath.open('wb') as out, errpath.open('wb') as err:
            process = subprocess.Popen([executable, *argv[1:]], cwd=jail, env=env,
                                       stdout=out, stderr=err, start_new_session=True)
            reason = 'exited'
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                reason = 'command_timeout'
                code = None
            finally:
                # Kill descendants too, including after their parent exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        changed = [p for p, pin in before.items()
                   if not confined(root, p).is_file() or sha(confined(root, p)) != pin
                   or not confined(jail, p).is_file() or sha(confined(jail, p)) != pin]
        changed += [p for p, pin in private_before.items() if sha(Path(trusted_files[p])) != pin
                    or sha(confined(jail, p)) != pin]
        if changed:
            raise PolicyError('command modified read-only mount: ' + ','.join(changed))
        for p in writes:
            src = confined(jail, p)
            if src.is_file():
                atomic_write(confined(root, p), src.read_bytes())
        import base64
        stdout, stderr = outpath.read_bytes(), errpath.read_bytes()
        return {'exit_code': code, 'reason': reason, 'seconds': time.monotonic()-started,
                'stdout': stdout.decode('utf-8', errors='replace'),
                'stderr': stderr.decode('utf-8', errors='replace'),
                'stdout_base64': base64.b64encode(stdout).decode(),
                'stderr_base64': base64.b64encode(stderr).decode(),
                'containment': 'degraded:staged-cwd-argv-pins;not-os-isolation'}
