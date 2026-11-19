"""Bounded single-process native-thread resources (not campaign concurrency)."""
import os

MAX_SOLVER_THREADS = 8


def available_threads():
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    return min(MAX_SOLVER_THREADS, cpus)


def validate_threads(threads):
    if type(threads) is not int:
        raise TypeError("solver threads must be an integer, not a boolean")
    if not 1 <= threads <= available_threads():
        raise ValueError("solver threads exceed the supported affinity/resource limit")
    return threads


def nn_scratch_bytes(n_cities, starts, threads):
    # Each concurrent kernel owns candidate/visited/best arrays; include retained
    # chunk outputs, copied starts and conservative ndarray element overhead.
    # This is an array estimate, not a process RSS or thread-stack hard quota.
    return min(starts, threads) * (32 * n_cities + 1024) + 8 * starts
