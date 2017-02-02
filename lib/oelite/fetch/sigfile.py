from collections import MutableMapping
import os
import fcntl

class SignatureFile(MutableMapping):

    def __init__(self, filename):
        self.filename = filename
        self.signatures = {}
        if os.path.exists(filename):
            with open(self.filename, "r") as sigfile:
                fcntl.flock(sigfile.fileno(), fcntl.LOCK_EX)
                self.read_lines(sigfile)

    def __getitem__(self, key): # required by Mapping
        return self.signatures[key]

    def __setitem__(self, key, value): # required by MutableMapping
        self.signatures[key] = value
        return value

    def __delitem__(self, key): # required by MutableMapping
        del self.signatures[key]
        return

    def __len__(self): # required by Sized
        return len(self.signatures)

    def __iter__(self): # required by Iterable
        return self.signatures.__iter__()

    def read_lines(self, f):
        for sigline in f:
            signature, localname = sigline.strip().split(None, 1)
            # Should we warn if a conflicting value for localname exists?
            self.signatures[localname] = signature

    def write(self):
        fd = os.open(self.filename, os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+") as sigfile:
            self.read_lines(sigfile)
            os.ftruncate(sigfile.fileno(), 0)
            sigfile.seek(0)
            localnames = self.signatures.keys()
            localnames.sort()
            for localname in localnames:
                sigfile.write("%s  %s\n"%(self.signatures[localname],
                                          localname))
