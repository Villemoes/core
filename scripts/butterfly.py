#!/usr/bin/python
#
# File: butterfly.py
# Time-stamp: <2017-01-26 23:16:02 villemoes>
# Author: Rasmus Villemoes

import fcntl
import filecmp
import mmap
import os
import random
import shutil
import subprocess
import sys
from stat import *
from collections import defaultdict

devnull = os.open("/dev/null", os.O_WRONLY)
fcntl.fcntl(devnull, fcntl.F_SETFD, fcntl.FD_CLOEXEC)

def listdir(d):
    ds, fs = [], []
    for root, dirs, files in os.walk(d):
        root = root[len(d)+1:]
        ds += [os.path.join(root, x) for x in dirs]
        fs += [os.path.join(root, x) for x in files]
    return set(ds), set(fs)

def treecmp(d1, d2):
    diffs = set()
    dirs1, files1 = listdir(d1)
    dirs2, files2 = listdir(d2)
    common_dirs = dirs1 & dirs2
    common_files = files1 & files2
    if common_dirs != dirs1 or common_dirs != dirs2:
        diffs.add("dirs")
    if common_files != files1 or common_files != files2:
        diffs.add("files")
    for f in (common_dirs | common_files):
        f1 = os.path.join(d1, f)
        f2 = os.path.join(d2, f)
        s1 = os.lstat(f1)
        s2 = os.lstat(f2)
        if s1.st_mtime != s2.st_mtime:
            diffs.add("mtime")
        if s1.st_mode != s2.st_mode:
            diffs.add("mode")
        else:
            # Compare contents only for symlinks and regular files
            if S_ISREG(s1.st_mode):
                if not filecmp.cmp(f1, f2, shallow=False):
                    diffs.add("file contents")
            elif S_ISLNK(s1.st_mode):
                if os.readlink(f1) != os.readlink(f2):
                    diffs.add("symlink target")
    if diffs:
        return ",".join(sorted(list(diffs)))
    return "(nothing)"
        
def do_flip(mm, bitnr):
    bytenr = bitnr >> 3
    bit = 1 << (bitnr & 7)
    mm[bytenr] = chr(ord(mm[bytenr]) ^ bit)
    pgbnd = bytenr & ~4095
    mm.flush(pgbnd, bytenr - pgbnd + 1)
    

def butterfly(mm):
    """Flip a random bit in the memory map mm. Returns the index of the
    flipped bit."""

    bitnr = random.randrange(0, 8*len(mm))
    do_flip(mm, bitnr)
    return bitnr

def do_mmap(fn):
    fd = os.open(fn, os.O_RDWR)
    st = os.fstat(fd)
    mm = mmap.mmap(fd, st.st_size, mmap.MAP_SHARED)
    os.close(fd)
    return mm

def extract(fn, d):
    shutil.rmtree(d, ignore_errors = True)
    os.mkdir(d)
    return subprocess.call(["/bin/tar", "xf", fn, "-C", d], stdout = devnull, stderr = devnull)

def do_random_flips(mm, fn, count = 100):
    undetected = 0
    diffstat = defaultdict(int)
    for i in xrange(count):
        bitnr = butterfly(mm)
        ret = extract(fn, "test")
        if ret == 0:
            undetected += 1
            diffstat[treecmp("orig", "test")] += 1
        do_flip(mm, bitnr)
    if undetected:
        print "%d of %d random flips were not detected by 'tar xf'." % (undetected, count)
        print "This resulted in these discrepancies in the unpacked tarball:"
        for d in diffstat:
            print "    %s: %d" % (d, diffstat[d])
    else:
        print "All %d bitflips detected by 'tar xf'" % count
    print
    return undetected

formats = [(":", ""),
           ("bzip2 -k -f", ".bz2"),
           ("gzip -k -f", ".gz"),
           ("lzip -k -f", ".lz"),
           ("xz -k -f", ".xz")]
output = open("results.txt", "w")
output.write("input/format\t%s\n" % "\t".join([".tar" + fmt[1] for fmt in formats]))

def do_file(src):
    shutil.copy(src, ".")
    src = os.path.basename(src)
    if extract(src, "orig"):
        print "%s seems to be corrupt! ignoring" % src
        return

    results = []
    for fmt in formats:
        subprocess.check_call("%s %s" % (fmt[0], src), shell=True)
        fn = src + fmt[1]
        print "doing", fn
        mm = do_mmap(fn)
        results.append(do_random_flips(mm, fn, 10))
        mm.close()
    for fmt in formats:
        os.unlink(src + fmt[1])
    output.write("%s\t%s\n" % (src, "\t".join([str(r) for r in results])))

for fn in sys.argv[1:]:
    do_file(fn)
#    butterfly
