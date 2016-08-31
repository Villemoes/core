import os
from stat import *

TOPDIR = os.getcwd()


def init(topdir):
    global TOPDIR
    TOPDIR = topdir


def relpath(path):
    """Return a relative version of paths compared to TOPDIR."""
    global TOPDIR
    if path.startswith(TOPDIR):
        return path[len(TOPDIR):].lstrip("/")
    return path


def which(path, filename, pathsep=os.pathsep):
    """Given a search path, find file."""
    if isinstance(path, basestring):
        path = path.split(pathsep)
    for p in path:
        f = os.path.join(p, filename)
        if os.path.exists(f):
            return os.path.abspath(f)
    return '' # TODO: change to None, and fixup the breakage it causes

class StatCache:
    def __init__(self, fn):
        self.fn = fn
        self._lstat = None
        self._stat = None

    # os.path.is* and os.path.[l]exists ignore all OSError exceptions,
    # not just ENOENT, so we do as well.
    def lstat(self):
        if self._lstat is None:
            try:
                self._lstat = os.lstat(self.fn)
            except OSError:
                self._lstat = False
        return self._lstat

    def stat(self):
        if self._stat is None:
            # If we've done an lstat and that didn't find a file, stat() won't either.
            if self._lstat is False:
                self._stat = False
            # If we've done a succesful lstat and that found a
            # non-link, we can reuse the stat structure.
            elif self._lstat is not None and not S_ISLNK(self._lstat.st_mode):
                self._stat = self._lstat
            else:
                # OK, either we haven't done an lstat or the file is a link. Gotta do stat.
                try:
                    self._stat = os.stat(self.fn)
                except OSError:
                    self._stat = False
        return self._stat

    def _answer(self, statfunc, predicatefunc):
        s = statfunc()
        if s is False:
            return False
        return predicatefunc(s.st_mode)

    def islink(self):
        return self._answer(self.lstat, S_ISLNK)

    def isdir(self):
        return self._answer(self.stat, S_ISDIR)

    def isfile(self):
        return self._answer(self.stat, S_ISREG)

    def exists(self):
        return self._answer(self.stat, lambda x: True)

    def lexists(self):
        return self._answer(self.lstat, lambda x: True)

def statcache(fn):
    return StatCache(fn)
