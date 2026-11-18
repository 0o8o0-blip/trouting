"""Run a function over items in forked worker processes, results as they come.

Python's multiprocessing pool needs POSIX semaphores, which KiCad's snap (where
the plugin runs) doesn't allow. This needs only fork and pipes: each worker
gets the parent's memory (the board, built once) and sends its result back,
pickled, through a pipe.
"""
import os
import pickle
import select
import signal
import time
import traceback


class Timeout(Exception):
    """The deadline passed; .left = how many items were still running or waiting."""

    def __init__(self, left):
        super().__init__("%d left" % left)
        self.left = left


def pmap(fn, items, jobs, deadline=lambda: None):
    """Yield fn(item) for each item, in the order they finish, at most `jobs` at
    once. deadline() is asked again and again: a time.time() after which the
    workers still running are stopped (raises Timeout), or None."""
    pending = list(items)
    running = {}                                   # read fd -> [pid, chunks]
    try:
        while pending or running:
            while pending and len(running) < max(1, jobs):
                item = pending.pop(0)
                r, w = os.pipe()
                pid = os.fork()
                if pid == 0:                           # the worker
                    os.close(r)
                    try:
                        out = ("ok", fn(item))
                    except BaseException:
                        out = ("err", traceback.format_exc())
                    try:
                        data = pickle.dumps(out)
                        with os.fdopen(w, "wb") as fh:
                            fh.write(data)
                    finally:
                        os._exit(0)
                os.close(w)
                running[r] = [pid, []]
            dl = deadline()
            wait = None if dl is None else max(0.0, dl - time.time())
            ready, _, _ = select.select(list(running), [], [], wait)
            if not ready:
                raise Timeout(len(running) + len(pending))
            for fd in ready:
                chunk = os.read(fd, 1 << 20)
                if chunk:
                    running[fd][1].append(chunk)
                    continue
                pid, chunks = running.pop(fd)
                os.close(fd)
                os.waitpid(pid, 0)
                status, val = pickle.loads(b"".join(chunks)) if chunks else ("err", "a worker died")
                if status == "err":
                    raise RuntimeError("worker failed:\n" + val)
                yield val
    finally:                                       # stopped early (deadline, error, caller gone)
        for fd, (pid, _) in running.items():
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
            os.close(fd)
