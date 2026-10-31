"""A real wall-clock supervisor. Children never outlive their task process group."""
import json
import os
import signal
import subprocess
import time
import threading
from pathlib import Path


_ACTIVE=set()
_LOCK=threading.Lock()
_ABORT=threading.Event()


def terminate_all():
    with _LOCK:
        _ABORT.set()
        processes=list(_ACTIVE)
    for process in processes:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass


def reset_cancellation():
    with _LOCK:
        if _ACTIVE:raise RuntimeError('cannot reset cancellation while children are active')
        _ABORT.clear()


def supervise(command, directory, seconds, cwd=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if seconds<=0:raise ValueError('seconds must be positive')
    clock=time.monotonic()
    with (directory/'worker.log').open('w') as log:
        with _LOCK:
            if _ABORT.is_set():raise RuntimeError('batch cancelled')
            process=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            _ACTIVE.add(process)
        killed=False
        try:code=process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            killed=True
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:code=process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                code=process.wait()
    with _LOCK:_ACTIVE.discard(process)
    outcome={'returncode':code,'hard_timeout':killed,'cancelled':_ABORT.is_set(),'wall_seconds':time.monotonic()-clock}
    (directory/'supervision.json').write_text(json.dumps(outcome,indent=2))
    return outcome
