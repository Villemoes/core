# This module is responsible for compiling various C extension modules
# found in lib/oelite. That may fail for a number of reasons:
#
# - lack of Python development headers
# - lack of suitable compiler (we assume gcc)
# - read-only lib/oelite directory
# - ... countless other things
#
# Therefore, all the C extensions are optional, and we have suitable
# fallbacks in the Python code that imports those.
#
import os
import sys
from subprocess import check_output, STDOUT, CalledProcessError

def _uptodate(dst, src):
    try:
        dt = os.path.getmtime(dst)
    except OSError:
        return False
    try:
        st = os.path.getmtime(src)
    except OSError:
        return False
    return dt > st

def _rm_dash_f(f):
    try:
       os.unlink(f)
    except OSError:
        pass

def compile_extension(dst, src, extra = ""):
    cwd = os.getcwd()
    fstr = None
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        failfile = os.path.join(d, os.path.dirname(dst),
                                "." + os.path.basename(dst) + ".failed")
        os.chdir(d)

        if _uptodate(dst, src):
            return

        # If a previous build was successful but the source has since
        # been updated, remove dst to ensure the caller doesn't end up
        # using a stale version. The Python fallback code can only
        # reasonably be expected to handle "either the current version
        # of the C extension module exists, or it doesn't exist at
        # all".
        _rm_dash_f(dst)

        pyver = sys.version_info
        args = {}
        args["dst"] = dst
        args["src"] = src
        args["extra"] = extra
        args["pyver"] = "%d.%d" % (pyver[0], pyver[1])
        args["warn"] = "-Wall -Wextra -Werror"
        cmd = "gcc {warn} -g -O2 -o {dst} -fPIC -shared -I/usr/include/python{pyver} {src} {extra}".format(**args)
        check_output(cmd, shell=True, stdin=None, stderr=STDOUT)
    except OSError as e:
        fstr = str(e).strip()
    except CalledProcessError as e:
        fstr = str(e) + "\n" + e.output.strip()
    except Exception as e:
        fstr = repr(e)
    else:
        # On success, delete an old failfile if it exists
        _rm_dash_f(failfile)
    finally:
        os.chdir(cwd)
    # This is probably overly verbose, but being completely silent
    # means people might never benefit from the faster C extensions,
    # which would be a pity if it's just a matter of doing 'apt-get
    # install python-dev'. We compromise and issue the warning once,
    # leaving behind a cookie so that we know not to bother the user
    # again.
    if fstr and not os.path.exists(failfile):
        sys.stderr.write("Failed to compile extension module %s:\n" % dst)
        sys.stderr.write(",---------------------------\n")
        for l in fstr.split("\n"):
            sys.stderr.write("| " + l + "\n")
        sys.stderr.write("`---------------------------\n")
        sys.stderr.write("OE-lite will still work, just a bit slower.\n")
        try:
            os.close(os.open(failfile, os.O_CREAT | os.O_WRONLY, 0o644))
        except OSError:
            pass
        else: 
            sys.stderr.write("This warning is printed only once.\n")
