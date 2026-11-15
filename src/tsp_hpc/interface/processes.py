"""Linux process identity and pidfd operations; never signal a bare recovered PID."""

import errno
import os
import re
from pathlib import Path
import select
import signal
import sys

WORKER_MODULE = "tsp_hpc.interface.worker"


def process_identity(pid, job_id):
    """Observe boot/start ticks and the exact trusted worker invocation marker."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        root = Path("/proc") / str(pid)
        with (root / "stat").open("rb") as stream:
            raw = stream.read(8192)
        tail = raw[raw.rfind(b")") + 2:].split()
        start = int(tail[19])  # field 22, after parenthesized comm (which can contain spaces).
        with (root / "cmdline").open("rb") as stream:
            argv = stream.read(8192).split(b"\0")[:-1]
        expected = [b"-I", b"-B", b"-W", b"error", b"-m", WORKER_MODULE.encode(), job_id.encode("ascii")]
        if argv[1:8] != expected or os.path.realpath(os.fsdecode(argv[0])) != os.path.realpath(sys.executable):
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "start_ticks": start, "boot_id": boot}
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def valid_identity(identity):
    return (type(identity) is dict and set(identity) == {"pid", "start_ticks", "boot_id"}
            and type(identity["pid"]) is int and identity["pid"] > 0
            and type(identity["start_ticks"]) is int and identity["start_ticks"] >= 0
            and type(identity["boot_id"]) is str
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identity["boot_id"]) is not None)


def exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    events = poll.poll(0)
    if any(flags & (select.POLLNVAL | select.POLLERR) for _, flags in events):
        raise OSError(errno.EBADF, "Invalid worker process handle")
    return any(flags & select.POLLIN for _, flags in events)


def recover_pidfd(identity, job_id):
    if not valid_identity(identity):
        return None
    try:
        fd = os.pidfd_open(identity["pid"], 0)
    except OSError:
        return None
    if exited(fd) or process_identity(identity["pid"], job_id) != identity:
        os.close(fd)
        return None
    return fd


def kill_verified(fd):
    if not exited(fd):
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
