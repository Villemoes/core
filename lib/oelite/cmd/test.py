import errno
import os
import select
import shutil
import signal
import sys
import tempfile
import time
import unittest

import oelite.util

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

    # @unittest.expectedFailure
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

    
def run(options, args, config):
    suite = unittest.TestSuite()
    suite.addTest(MakedirsRaceTest())
    if options.show:
        for t in suite:
            print str(t), "--", t.shortDescription()
        return 0
    runner = unittest.TextTestRunner(verbosity=3)
    runner.run(suite)

    return 0
