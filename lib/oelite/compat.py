import os
import fcntl
import ctypes
import sys

__all__ = ["dup_cloexec", "open_cloexec"]

from . import cext
cext.compile_extension("_compat.so", "_compat.c")

def set_cloexec(fd):
    fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    return fd

try:
    from ._compat import F_DUPFD_CLOEXEC
    def dup_cloexec(fd):
        return fcntl.fcntl(fd, F_DUPFD_CLOEXEC, 0)
except ImportError:
    def dup_cloexec(fd):
        return set_cloexec(os.dup(fd))

try:
    from ._compat import O_CLOEXEC
    def open_cloexec(path, flag, mode=0777):
        return os.open(path, flag | O_CLOEXEC, mode)
except ImportError:
    def open_cloexec(path, flag, mode=0777):
        return set_cloexec(os.open(path, flag, mode))



def has_cloexec(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    return (flags & fcntl.FD_CLOEXEC) != 0

def test_open_cloexec():
    fd = open_cloexec("/dev/null", os.O_RDONLY)
    assert(has_cloexec(fd))
    os.close(fd)
    fd = open_cloexec("/dev/null", os.O_WRONLY)
    assert(has_cloexec(fd))
    os.close(fd)

def test_dup_cloexec():
    fd = dup_cloexec(sys.stdin.fileno())
    assert(has_cloexec(fd))
    os.close(fd)

# To run the tests, go to meta/core/lib, then execute
#
#   python -m oelite.compat
#
# It must be done that way to avoid "ValueError: Attempted relative
# import in non-package".

if __name__ == "__main__":
    test_open_cloexec()
    test_dup_cloexec()
