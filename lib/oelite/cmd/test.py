import errno
import fcntl
import hashlib
import os
import select
import shutil
import signal
import sys
import tempfile
import time
import unittest

import oelite.util
import oelite.signal
import oelite.compat

description = "Run tests of internal utility functions"
def add_parser_options(parser):
    parser.add_option("-s", "--show",
                      action="store_true", default=False,
                      help="Show list of tests")

class OEliteTest(unittest.TestCase):
    def setUp(self):
        self.wd = tempfile.mkdtemp()
        os.chdir(self.wd)

    def tearDown(self):
        os.chdir("/")
        shutil.rmtree(self.wd)

    def test_makedirs(self):
        """Test the semantics of oelite.util.makedirs"""

        makedirs = oelite.util.makedirs
        touch = oelite.util.touch
        self.assertIsNone(makedirs("x"))
        self.assertIsNone(makedirs("x"))
        self.assertIsNone(makedirs("y/"))
        self.assertIsNone(makedirs("y/"))
        self.assertIsNone(makedirs("x/y/z"))
        # One can create multiple leaf directories in one go; mkdir -p
        # behaves the same way.
        self.assertIsNone(makedirs("z/.././z//w//../v"))
        self.assertTrue(os.path.isdir("z/w"))
        self.assertTrue(os.path.isdir("z/v"))

        self.assertIsNone(touch("x/a"))
        with self.assertRaises(OSError) as cm:
            makedirs("x/a")
        self.assertEqual(cm.exception.errno, errno.ENOTDIR)
        with self.assertRaises(OSError) as cm:
            makedirs("x/a/z")
        self.assertEqual(cm.exception.errno, errno.ENOTDIR)

        self.assertIsNone(os.symlink("a", "x/b"))
        with self.assertRaises(OSError) as cm:
            makedirs("x/b")
        self.assertEqual(cm.exception.errno, errno.ENOTDIR)
        with self.assertRaises(OSError) as cm:
            makedirs("x/b/z")
        self.assertEqual(cm.exception.errno, errno.ENOTDIR)

        self.assertIsNone(os.symlink("../y", "x/c"))
        self.assertIsNone(makedirs("x/c"))
        self.assertIsNone(makedirs("x/c/"))

        self.assertIsNone(os.symlink("nowhere", "broken"))
        with self.assertRaises(OSError) as cm:
            makedirs("broken")
        self.assertEqual(cm.exception.errno, errno.ENOENT)

        self.assertIsNone(os.symlink("loop1", "loop2"))
        self.assertIsNone(os.symlink("loop2", "loop1"))
        with self.assertRaises(OSError) as cm:
            makedirs("loop1")
        self.assertEqual(cm.exception.errno, errno.ELOOP)

    def test_cloexec(self):
        open_cloexec = oelite.compat.open_cloexec
        dup_cloexec = oelite.compat.dup_cloexec

        def has_cloexec(fd):
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            return (flags & fcntl.FD_CLOEXEC) != 0

        fd = open_cloexec("/dev/null", os.O_RDONLY)
        self.assertGreaterEqual(fd, 0)
        self.assertTrue(has_cloexec(fd))

        fd2 = os.dup(fd)
        self.assertGreaterEqual(fd2, 0)
        self.assertFalse(has_cloexec(fd2))
        self.assertIsNone(os.close(fd2))

        fd2 = dup_cloexec(fd)
        self.assertGreaterEqual(fd2, 0)
        self.assertTrue(has_cloexec(fd2))
        self.assertIsNone(os.close(fd2))

        self.assertIsNone(os.close(fd))

    def test_hash_file(self):
        testv = [(0, "d41d8cd98f00b204e9800998ecf8427e", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
                 (1, "0cc175b9c0f1b6a831c399e269772661", "86f7e437faa5a7fce15d1ddcb9eaeaea377667b8"),
                 (1000, "cabe45dcc9ae5b66ba86600cca6b8ba8", "291e9a6c66994949b57ba5e650361e98fc36b1ba"),
                 (1000000, "7707d6ae4e027c70eea2a935c2296f21", "34aa973cd4c4daa4f61eeb2bdbad27316534016f")]
        hash_file = oelite.util.hash_file

        for size, md5, sha1 in testv:
            # open and say "aaaa...." :-)
            with tempfile.NamedTemporaryFile() as tmp:
                self.assertIsNone(tmp.write("a"*size))
                self.assertIsNone(tmp.flush())
                self.assertEqual(os.path.getsize(tmp.name), size)

                h = hash_file(hashlib.md5(), tmp.name).hexdigest()
                self.assertEqual(h, md5)

                h = hash_file(hashlib.sha1(), tmp.name).hexdigest()
                self.assertEqual(h, sha1)

class MakedirsRaceTest(OEliteTest):
    def child(self):
        signal.alarm(2) # just in case of infinite recursion bugs
        try:
            # wait for go
            select.select([self.r], [], [], 1)
            oelite.util.makedirs(self.path)
            # no exception? all right
            res = "OK"
        except OSError as e:
            # errno.errorcode(errno.ENOENT) == "ENOENT" etc.
            res = errno.errorcode.get(e.errno) or str(e.errno)
        except Exception as e:
            res = "??"
        finally:
            # Short pipe writes are guaranteed atomic
            os.write(self.w, res+"\n")
            os._exit(0)

    def setUp(self):
        super(MakedirsRaceTest, self).setUp()
        self.path = "x/" * 10
        self.r, self.w = os.pipe()
        self.children = []
        for i in range(8):
            pid = os.fork()
            if pid == 0:
                self.child()
            self.children.append(pid)

    def runTest(self):
        """Test concurrent calls of oelite.util.makedirs"""

        os.write(self.w, "go go go\n")
        time.sleep(0.01)
        os.close(self.w)
        with os.fdopen(self.r) as f:
            v = [v.strip() for v in f]
        d = {x: v.count(x) for x in v if x != "go go go"}
        # On failure this won't give a very user-friendly error
        # message, but it should contain information about the errors
        # encountered.
        self.assertEqual(d, {"OK": len(self.children)})
        self.assertTrue(os.path.isdir(self.path))

    def tearDown(self):
        for pid in self.children:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        super(MakedirsRaceTest, self).tearDown()
    
class SigPipeTest(OEliteTest):
    def run_sub(self, preexec_fn):
        from subprocess import PIPE, Popen

        sub = Popen(["yes"], stdout=PIPE, stderr=PIPE,
                    preexec_fn = preexec_fn)
        # Force a broken pipe.
        sub.stdout.close()
        err = sub.stderr.read()
        ret = sub.wait()
        return (ret, err)

    @unittest.skipIf(sys.version_info >= (3, 2), "Python is new enough")
    def test_no_restore(self):
        """Check that subprocesses inherit the SIG_IGNORE disposition for SIGPIPE."""
        (ret, err) = self.run_sub(None)
        # This should terminate with a write error; we assume that
        # 'yes' is so well-behaved that it both exits with a non-zero
        # exit code as well as prints an error message containing
        # strerror(errno).
        self.assertGreater(ret, 0)
        self.assertIn(os.strerror(errno.EPIPE), err)

    def test_restore(self):
        """Check that oelite.signal.restore_defaults resets the SIGPIPE disposition."""
        (ret, err) = self.run_sub(oelite.signal.restore_defaults)
        # This should terminate due to SIGPIPE, and not get a chance
        # to write to stderr.
        self.assertEqual(ret, -signal.SIGPIPE)
        self.assertEqual(err, "")

def run(options, args, config):
    suite = unittest.TestSuite()
    suite.addTest(MakedirsRaceTest())
    suite.addTest(OEliteTest('test_makedirs'))
    suite.addTest(SigPipeTest('test_no_restore'))
    suite.addTest(SigPipeTest('test_restore'))
    suite.addTest(OEliteTest('test_cloexec'))
    suite.addTest(OEliteTest('test_hash_file'))

    if options.show:
        for t in suite:
            print str(t), "--", t.shortDescription()
        return 0
    runner = unittest.TextTestRunner(verbosity=3)
    runner.run(suite)

    return 0
