import os
from stat import *
import errno
import shutil

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
    """Lazy and caching os.[l]stat wrapper."""

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

def copy_dentry(src, dst, recursive = False, hardlink = True):
    """Make dst a copy of src. Symbolic links are created with the same
    contents, directories with the same permissions, and regular files
    are hard linked, thus preserving all attributes. It is up to the
    caller to ensure that os.path.dirname(dst) exists and that dst
    doesn't.

    Returns list of source dentries copied (which may be more than
    [src] if src is a directory and recursive is True).

    """

    sstat = os.lstat(src)

    ret = [src]
    if S_ISDIR(sstat):
        os.mkdir(dst)
        os.chmod(dst, sstat.st_mode)
        # This assumes the caller doesn't try something stupid such as
        # copying a directory tree into itself.
        if recursive:
            for x in os.listdir(src):
                ret += copy_dentry(os.path.join(src, x),
                                   os.path.join(dst, x),
                                   recursive = True,
                                   hardlink = hardlink)

    elif S_ISREG(sstat):
        do_fallback = True
        if hardlink:
            try:
                os.link(src, dst)
                do_fallback = False
            except OSError as e:
                # We may encounter EPERM if we're on a non-posix
                # compliant file system, and EMLINK in some extremely
                # weird case otherwise (I don't think there's any
                # hardlink-allowing filesystem that has _PC_LINK_MAX
                # less than 126). In those cases, fall through to a
                # regular copy.
                if e.errno not in (errno.EMLINK, errno.EPERM):
                    raise
        if do_fallback:
            shutil.copyfile(src, dst)
            os.chmod(dst, sstat.st_mode)

    elif S_ISLNK(sstat):
        target = os.readlink(src)
        os.symlink(target, dst)

    # We probably don't really need to handle S_ISBLK, S_ISCHR,
    # S_ISFIFO, S_ISSOCK, and even if we try, we'll probably fail with
    # EPERM. Anyway, for completeness:
    elif S_ISBLK(sstat) or S_ISCHR(sstat) or S_ISFIFO(sstat) or S_ISSOCK(sstat):
        # mknod ignores the device arg for FIFO and SOCK
        os.mknod(dst, S_IFMT(sstat) | S_IMODE(sstat), sstat.st_rdev)
        os.chmod(dst, sstat.st_mode)

    else:
        raise OSError(errno.EINVAL, "unknown file type %08o" % sstat.st_mode, src)

    return ret
