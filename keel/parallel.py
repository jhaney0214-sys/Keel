"""Independent model passes, run across the computer's cores.

A report runs the same book through a dozen or more scenarios, and the
key-assumption tests through a dozen variants; none depends on another.
`run` takes a list of tasks, each a function in Keel (by its dotted name)
and its arguments, and returns their results in order: in worker processes
when there are enough tasks to repay starting them, otherwise, or if
anything cannot be sent to a worker, one after another in this process.
The answers are the same either way; only the time differs.

`KEEL_WORKERS=1` (an environment variable) turns it off; `KEEL_WORKERS=n`
sets the number of workers. Only the standard library is used.
"""

import atexit
import concurrent.futures
import importlib
import os
import pickle
import sys

MIN_TASKS = 3            # below this, starting workers costs more than it saves
_pool = None
_size = 0


def workers():
    setting = os.environ.get("KEEL_WORKERS", "").strip()
    if setting.isdigit():
        return max(1, int(setting))
    return max(1, min((os.cpu_count() or 1) - 1, 12))


def _spawnable():
    """Whether worker processes can start: on Windows each re-imports the
    program that launched it, which a script piped to Python or typed into a
    notebook does not have. Found when a piped script printed a traceback
    from every worker before falling back."""
    main = sys.modules.get("__main__")
    if getattr(main, "__spec__", None) is not None:          # python -m keel, or an installed command
        return True
    path = getattr(main, "__file__", None)
    return bool(path) and os.path.isfile(path)


def _call(task):
    name, args, kwargs = task
    module, _, function = name.rpartition(".")
    if not module.startswith("keel."):
        raise ValueError("parallel: only Keel's own functions run in workers, not %r" % name)
    return getattr(importlib.import_module(module), function)(*args, **kwargs)


def _get_pool(n):
    global _pool, _size
    if _pool is None or _size != n:
        shutdown()
        _pool = concurrent.futures.ProcessPoolExecutor(max_workers=n)
        _size = n
    return _pool


def shutdown():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None


atexit.register(shutdown)


def run(tasks):
    """[result] for [(dotted function name, args tuple, kwargs dict)], in order."""
    tasks = [(name, tuple(args), dict(kwargs or {})) for name, args, kwargs in tasks]
    n = min(workers(), len(tasks))
    if n > 1 and len(tasks) >= MIN_TASKS and _spawnable():
        try:
            return list(_get_pool(n).map(_call, tasks))
        except (OSError, pickle.PicklingError, AttributeError, TypeError,
                concurrent.futures.process.BrokenProcessPool):
            shutdown()          # fall through: the same work, in this process
    return [_call(task) for task in tasks]
