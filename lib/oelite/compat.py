#!/usr/bin/python
import os
import errno
import fcntl
import ctypes
import sys

__all__ = ["dup_cloexec", "open_cloexec", "copyfile"]

# uname() -> (sysname, nodename, release, version, machine)
sysname, _, release, _, machine = os.uname()

values = dict()
if (sysname, machine) == ("Linux", "x86_64"):
    # these are supported since at least 2.6.24, so just set them unconditionally
    values['O_CLOEXEC'] = 02000000
    values['F_DUPFD_CLOEXEC'] = 1024+6

# import bb.utils doesn't work when we try to run this by itself (to
# test it), so this just serves to show what one could do, provided
# someone figures out how to make the vercmp_string available.
#
# elif sysname == "FreeBSD":
#     if bb.utils.vercmp_string(release, "8.3") >= 0:
#         values['O_CLOEXEC'] = 0x00100000
#     if bb.utils.vercmp_string(release, "9.2") >= 0:
#         values['F_DUPFD_CLOEXEC'] = 17

def dup_cloexec(fd):
    return fcntl.fcntl(fd, F_DUPFD_CLOEXEC, 0)

def open_cloexec(filename, flag, mode=0777):
    return os.open(filename, flag | O_CLOEXEC, mode)

def set_cloexec(fd):
    fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    return fd

def dup_cloexec_fallback(fd):
    return set_cloexec(os.dup(fd))

def open_cloexec_fallback(filename, flag, mode=0777):
    return set_cloexec(os.open(filename, flag, mode))

if hasattr(os, "O_CLOEXEC"):
    O_CLOEXEC = os.O_CLOEXEC
else:
    try:
        O_CLOEXEC = values["O_CLOEXEC"]
    except KeyError:
        open_cloexec = open_cloexec_fallback

if hasattr(fcntl, "F_DUPFD_CLOEXEC"):
    F_DUPFD_CLOEXEC = fcntl.F_DUPFD_CLOEXEC
else:
    try:
        F_DUPFD_CLOEXEC = values["F_DUPFD_CLOEXEC"]
    except KeyError:
        dup_cloexec = dup_cloexec_fallback


# os.sendfile exists from Python 3.3, but the "offset" argument seems
# to be handled a little weird (it certainly cannot behave as the
# underlying system call), and we don't really need it anyway. Also,
# implementing a fallback supporting it is prohibitively hard since
# os.pread also doesn't exist until 3.3.
def sendfile_fallback(dfd, sfd, count):
    buf = os.read(sfd, count)
    count = len(buf)
    if count == 0:
        return 0
    written = 0
    try:
        # Attempt a single write of the whole buffer first, to avoid
        # Python creating a copy of the buffer before passing it to
        # os.write, as it probably will inside the loop.
        written += os.write(dfd, buf)
        while written < count:
            written += os.write(dfd, buf[written:])
        return written
    except Exception as e:
        # If something went wrong writing the buffer, try to reset the
        # input file descriptor so the data can be read again.
        try:
            os.lseek(sfd, -(count - written), os.SEEK_CUR)
        except:
            pass
        raise e

# FIXME: Implement a ctypes-based sendfile that will actually use the
# underlying system call.
sendfile = sendfile_fallback

def copyfile(src, dst, mode=0666):
    """Copy a regular file from src to dst. If possible, the atime of src
    is not modified. If dst exists, it must be a regular file (which
    will be truncated and overwritten) - a symbolic link is _not_
    followed. Otherwise, the caller must ensure that dirname(dst)
    exists. The caller must also ensure that src and dst do not name
    the same file (otherwise it will be silently truncated). Returns
    True on success, propagates exception on failure.

    """
    try:
        sfd = os.open(src, os.O_RDONLY | os.O_NOATIME)
    except OSError as e:
        if e.errno == errno.EPERM:
            sfd = os.open(src, os.O_RDONLY)
        else:
            raise
    try:
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        try:
            while True:
                ret = sendfile(dfd, sfd, 1 << 14)
                if ret == 0:
                    return True
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)

# Very simple selftests follow:

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

def test_copyfile():
    import tempfile
    import filecmp
    import unittest
    # If you're strace'ing to see what this does, it may be useful to do
    #
    #   strace -e 'trace=!read,write' ./compat.py
    #
    # to avoid lots of clutter.
    def create_tmpfile(size):
        f = tempfile.NamedTemporaryFile(prefix="copytest_%d_" % size, delete=False)
        name = f.name
        while size > 0:
            w = min(size, 1<<14)
            f.write("x"*w)
            size -= w
        f.close()
        return name

    class CopyTest(unittest.TestCase):
        def __init__(self, methodName='runTest', **param):
            super(CopyTest, self).__init__(methodName)
            self.size = param['size']
            self.xdev = param['xdev']
            self.param = param
        def setUp(self):
            self.src = create_tmpfile(self.size)
            self.dst = self.src + ".copy"
            if self.xdev:
                # Just use the current working directory - we assume
                # /tmp and $cwd are on different devices, and that we
                # can actually create files in $cwd. The generated
                # file name should be unique enough.
                self.dst = os.path.basename(self.dst)
            if os.path.lexists(self.dst):
                os.unlink(self.src)
                self.skipTest("target %s already exists!" % self.dst)

        def tearDown(self):
            os.unlink(self.src)
            try:
                os.unlink(self.dst)
            except OSError:
                pass

        def do_copy(self):
            return copyfile(self.src, self.dst)

        def check_copy(self):
            return filecmp.cmp(self.src, self.dst)

        def __str__(self):
            name = self.__class__.__name__
            name += ": "
            name += self._testMethodName.replace("test_", "", 1)
            for p in sorted(self.param.keys()):
                name += ", %s=%s" % (p, self.param[p])
            return name

        def test_basic(self):
            self.assertTrue(self.do_copy())
            self.assertTrue(self.check_copy())

        def test_dst_exists(self):
            # Just do the basic test twice, without teardown
            self.test_basic()
            self.test_basic()

        def test_src_unreadable(self):
            os.chmod(self.src, 0)
            with self.assertRaises(OSError) as cm:
                self.do_copy()
            self.assertEqual(cm.exception.errno, errno.EACCES)

        def test_dst_dir_missing(self):
            self.dst = "/this/path/should/not/exist"
            with self.assertRaises(OSError) as cm:
                self.do_copy()
            self.assertEqual(cm.exception.errno, errno.ENOENT)

    suite = unittest.TestSuite()
    for size in (0, 1, 10, 100, 1000, 10000, 100*1000, 1000*1000):
        for method in (unittest.defaultTestLoader.getTestCaseNames(CopyTest)):
            test = CopyTest(methodName=method, size=size, xdev = False)
            suite.addTest(test)
            test = CopyTest(methodName=method, size=size, xdev = True)
            suite.addTest(test)
    runner = unittest.TextTestRunner(verbosity=3)
    runner.run(suite)

if __name__ == "__main__":
    test_open_cloexec()
    test_dup_cloexec()
    test_copyfile()
