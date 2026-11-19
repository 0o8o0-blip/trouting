"""Run a function over items in worker processes, results as they come.

Two ways, one interface:
- fork (Linux): Python's multiprocessing pool needs POSIX semaphores, which
  KiCad's snap (where the plugin runs) doesn't allow. This needs only fork and
  pipes: each worker gets the parent's memory (the board, built once) and sends
  its result back, pickled, through a pipe.
- spawn (Windows, which has no fork; macOS, where forking a process that has
  loaded Apple's frameworks, as numpy does, can crash the child): a
  multiprocessing pool. Each worker starts a fresh Python, so it runs
  init(*initargs) itself to build what it needs.

TROUTING_SPAWN=1 uses spawn where fork exists too (to test that path).
"""
import os
import pickle
import sys
import time
import traceback


class Timeout(Exception):
    """The deadline passed; .left = how many items were still running or waiting."""

    def __init__(self, left):
        super().__init__("%d left" % left)
        self.left = left


def use_fork():
    return hasattr(os, "fork") and sys.platform != "darwin" and not os.environ.get("TROUTING_SPAWN")


def pmap(fn, items, jobs, deadline=lambda: None, init=None, initargs=()):
    """Yield fn(item) for each item, in the order they finish, at most `jobs` at
    once. init(*initargs) prepares the workers (sets the globals fn reads); fn,
    init and the items must be picklable (module-level functions) for spawn.
    deadline() is asked again and again: a time.time() after which the workers
    still running are stopped (raises Timeout), or None."""
    if use_fork():
        if init:
            init(*initargs)
        return _pmap_fork(fn, items, jobs, deadline)
    return _pmap_spawn(fn, items, jobs, deadline, init, initargs)


def _pmap_fork(fn, items, jobs, deadline):
    import select
    import signal
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


def _call(fn, item):
    try:
        return ("ok", fn(item))
    except BaseException:
        return ("err", traceback.format_exc())


def _pmap_spawn(fn, items, jobs, deadline, init, initargs):
    import functools
    import multiprocessing as mp
    items = list(items)
    if not items:
        return
    n = max(1, min(jobs, len(items), 61))          # Windows waits on at most 63 handles
    pool = mp.get_context("spawn").Pool(n, initializer=init, initargs=initargs)
    try:
        it = pool.imap_unordered(functools.partial(_call, fn), items)
        for done in range(len(items)):
            dl = deadline()
            try:
                status, val = it.next(None if dl is None else max(0.0, dl - time.time()))
            except mp.TimeoutError:
                raise Timeout(len(items) - done)
            if status == "err":
                raise RuntimeError("worker failed:\n" + val)
            yield val
    finally:
        pool.terminate()                           # stops the workers still running
        pool.join()
