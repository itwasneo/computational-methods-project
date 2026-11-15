"""Linux process identity and pidfd operations; never signal a bare recovered PID."""

import os
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


def exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return bool(poll.poll(0))


def recover_pidfd(identity, job_id):
    if (type(identity) is not dict or set(identity) != {"pid", "start_ticks", "boot_id"}
            or type(identity["pid"]) is not int or identity["pid"] <= 0):
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
