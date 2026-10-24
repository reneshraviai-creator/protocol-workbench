"""One simulator writer per installation folder, on Windows or macOS/Linux."""
import os


def acquire(path):
    handle = open(path, "a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("A simulator is already running from this folder.")
    return handle


def running(path):
    try:
        handle = acquire(path)
    except RuntimeError:
        return True
    handle.close()
    return False
