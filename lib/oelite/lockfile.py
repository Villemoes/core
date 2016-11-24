import os
import fcntl
import errno

from fcntl import LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN, flock

from .compat import open_cloexec

def _oserror(err, txt):
    return OSError(err, os.strerror(err), txt)

# This is really a somewhat generic method for implementing timeouts
# on blocking system calls that don't natively support a timeout
# parameter. Set an itimer and, if the system call fails with EINTR,
# check if the timer fired - if so, change EINTR to ETIMEDOUT,
# otherwise restart the system call.
#
# In Python, the easiest way for a signal handler to be able to mutate
# state is for it to be an instance method.
#
# We want to ensure we reset the itimer and the signal handler, so
# this only supports being used as a context manager.
#
# We do not attempt to restore the old value of the ITIMER_REAL
# timer. Also, nesting these is obviously meaningless; only use them
# for wrapping a single system call.
#
# Finally, in Python, only the main thread can receive and handle a
# signal, so this doesn't mix well with threads. Fortunately, Python
# also enforces that only the main thread may call signal.signal, so
# if any non-main thread ever calls the __enter__ method it will get
# an exception, and thus never enter the with block.
class Timeout(object):
    def __init__(self, timeout):
        self.timeout = timeout

    def handler(self, signum, frame):
        self.expired = True

    # Py3
    def __bool__(self):
        return self.expired
    # Py2
    def __nonzero__(self):
        return self.__bool__()

    def __enter__(self):
        self.expired = False
        self.oldhandler = signal.signal(signal.SIGALRM, self.handler)
        signal.setitimer(signal.ITIMER_REAL, self.timeout)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Disable the timer before restoring the old signal handler -
        # if we're unlucky and the timer hasn't expired, it could do
        # so just after restoring the signal handler but before
        # disabling the timer. Since the default disposition for
        # SIGALRM (and hence presumably what we're setting the handler
        # back to) is to terminate the process, that would be a pity.
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.oldhandler)
        return False

# Even if we create the file during open(), we never attempt to unlink the file.
class LockFile(object):
    def __init__(self, name, flags = LOCK_EX):
        self.name = name
        # The flags is for use in a with statement, where we
        # can't otherwise specify the type of lock we want.
        self.flags = flags
        self.fd = -1
        self.close_on_unlock = False
        self.has_lock = False

    def __del__(self):
        if self.fd >= 0:
            self.close()

    def open(self):
        # Create the file if it doesn't exist, don't accept symlinks,
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        mode = 0o644
        assert(self.fd == -1)
        self.fd = open_cloexec(self.name, flags, mode)

    def close(self):
        assert(self.fd >= 0)
        os.close(self.fd)
        self.fd = -1

    def do_lock(self, flags = None, timeout = None):
        if flags is None:
            flags = self.flags
        # flags must be LOCK_SH or LOCK_EX, possibly ORed with LOCK_NB.
        if not (flags & ~LOCK_NB) in (LOCK_SH, LOCK_EX):
            raise _oserror(errno.EINVAL, "invalid flags")

        if timeout is not None and timeout <= 0:
            flags |= LOCK_NB

        assert(self.fd >= 0)

        # If the caller requested a non-blocking operation, or
        # requested an infinitely-blocking operation (by not passing a
        # timeout), we can simply call flock.
        if timeout is None or (flags & LOCK_NB):
            return flock(self.fd, flags)

        assert(timeout > 0)
        assert((flags & LOCK_NB) == 0)

        # Even if the user didn't pass LOCK_NB, we do one "trylock"
        # operation, so that we avoid mucking with signals in the
        # presumably very common case where we can get the lock
        # immediately.
        try:
            return flock(self.fd, flags | LOCK_NB)
        except OSError as e:
            pass

        with Timeout(timeout) as expired:
            while True:
                try:
                    return flock(self.fd, flags)
                except OSError as e:
                    if e.errno == errno.EINTR:
                        if expired:
                            break
                        continue
                    raise
        raise _oserror(errno.ETIMEDOUT, "timeout waiting for lock")

    def lock(self, flags = None, timeout = None):
        # flock(2) allows changing the lock type held, but it is not
        # guaranteed to happen atomically - e.g., if one holds an
        # exclusive lock, one isn't guaranteed that downgrading to a
        # shared lock won't block. So to simplify maintaining our view
        # of the world, we explicitly unlock and then lock. Note that
        # this means that if the caller passes LOCK_NB or a timeout,
        # the caller may lose a lock he used to hold.
        if self.has_lock:
            self.unlock()

        if self.fd == -1:
            self.open()
            self.close_on_unlock = True
        else:
            self.close_on_unlock = False

        try:
            self.do_lock(flags, timeout)
            self.has_lock = True
        except:
            if self.close_on_unlock:
                self.close()
            raise

    def unlock(self):
        if not self.has_lock:
            return # or raise? or let the flock call raise?
        flock(self.fd, LOCK_UN)
        self.has_lock = False
        if self.close_on_unlock:
            self.close()

    def __enter__(self):
        return self.lock() # returns None or raises exception

    def __exit__(self):
        self.unlock()

    def shared(self):
        self.flags = LOCK_SH
        return self

    def exclusive(self):
        self.flags = LOCK_EX
        return self

if __name__ == "__main__":
    pass
