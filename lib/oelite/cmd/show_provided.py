import oebakery
import oelite.baker
import logging
from oelite.parse import confparse
import re
import subprocess
import bb.utils

description = "Detect and print versions of provided utils"

def add_parser_options(parser):
    oelite.baker.add_show_parser_options(parser)
    parser.add_option("-v", "--verbose",
                      action="store_true",
                      help="Be more chatty")
    parser.add_option("-c", "--check",
                      action="store_true",
                      help="Check detected version against required min and max")
    return

def parse_args(options, args):
    if options.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

INITIAL_OE_IMPORTS = "sys os time"

# One must describe how to get the version of each util. Fortunately,
# many utilities support a simple "--version" flag, and for most, the
# command to call is the same as what's in the ASSUME_PROVIDED (an
# exception is native:pkgconfig, where the command is pkg-config). So
# we don't need to be very verbose.
#
# extract: a regexp string which is matched against the output from
# the command. Should contain a single capture group, from which we'll
# get the version string
#
# ret: Some utilities exit with a non-zero value when asked to print
# their version (sometimes because they don't actually explicitly
# support --version or any other flag, but they display their version
# nevertheless as a side effect of telling the user he did something
# wrong...). This can be set to an integer or list of integers of
# expected return values.
data = dict()
data["native:binutils"] = dict(cmd = "ld")
data["native:coreutils"] = dict(cmd = "ls")
data["native:mercurial"] = dict(cmd = "hg")
data["native:mtd-utils-mkfs-jffs2"] = dict(cmd = "mkfs.jffs2", ret = 255)
data["native:perl"] = dict(extract = r"\(v([0-9][0-9.a-zA-Z_-]+)\)")
data["native:pkgconfig"] = dict(cmd = "pkg-config")
data["native:python-runtime"] = dict(cmd = "python")
data["native:texinfo"] = dict(cmd = "texindex")
data["native:unzip"] = dict(args = "-v")
data["native:util-linux"] = dict(cmd = "getopt")

def fill_defaults(data, assume_provided):
    # Provide an empty dict for those where we don't have any
    # requirements. That makes it easy for us to (try to) run the
    # default "get the version provided", possibly fixing up those
    # where that fails.
    for name in assume_provided:
        if name in data:
            continue
        # Libraries would need to be handled in some other way...
        if name.startswith("native:lib"):
            continue
        data[name] = dict()
    for name in data:
        param = data[name]
        if not "cmd" in param:
            if ":" in name:
                param["cmd"] = name.split(":", 1)[1]
            else:
                param["cmd"] = name

        if not "args" in param:
            param["args"] = "--version"
        if isinstance(param["args"], basestring):
            param["args"] = [param["args"]]

        if not "extract" in param:
            param["extract"] = r"\b([0-9][0-9.a-zA-Z_-]+)\b"

        if not "ret" in param:
            param["ret"] = 0
        if (isinstance(param["ret"], int)):
            param["ret"] = [param["ret"]]

def fill_versions(data, key, versions):
    for util in versions:
        u, v = util.split("_", 1)
        if u in data:
            data[u][key] = v

def get_provided_version(p):
    param = data.get(p)
    if not param:
        return None

    args = [param["cmd"]] + param["args"]
    cmdstring = " ".join(args)
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        logging.warn("%s: failed to execute '%s': %s", p, cmdstring, e)
        return None
    output = process.communicate()[0]

    if process.returncode not in param["ret"]:
        logging.warn("%s: command '%s' exited with status %d, expected one of %s" %
                     (p, cmdstring, process.returncode, str(param["ret"])))
        if output:
            logging.debug("\n".join(map(lambda x: "  " + x, output.split("\n"))))
        return None

    match = re.search(param["extract"], output)
    if not match:
        logging.info("%s: regexp '%s' did not match output")
        if output:
            logging.warn("\n".join(map(lambda x: "  " + x, output.split("\n"))))
        return None
    return match.group(1)

def show_provided(p):
    version = get_provided_version(p)
    if version:
        logging.info("%s: found version %s", p, version)
    return None

def check_provided(p):
    param = data.get(p)
    if not param or (not param.get("min") and not param.get("max")):
        logging.debug("%s: no version requirements defined" % p)
        return None
    logging.debug("checking %s" % p)

    version = get_provided_version(p)

    logging.debug("%s: found version %s", p, version)
    minversion = param.get("min")
    maxversion = param.get("max")
    ok = True
    if minversion:
        if bb.utils.vercmp_string(version, param["min"]) < 0:
            logging.warn("%s: has version %s, expected minumum %s", p, version, minversion)
            ok = False
    if maxversion:
        if bb.utils.vercmp_string(version, param["max"]) > 0:
            logging.warn("%s: has version %s, expected maximum %s", p, version, maxversion)
            ok = False

    return ok

def run(options, args, config):
    ret = 0
    config = oelite.meta.DictMeta(config)
    config["OE_IMPORTS"] = INITIAL_OE_IMPORTS
    config.pythonfunc_init()

    confparser = confparse.ConfParser(config)
    confparser.parse("conf/oe-lite.conf")

    assume_provided = sorted(config.get("ASSUME_PROVIDED").split())
    fill_defaults(data, assume_provided)
    assume_min = (config.get("ASSUME_PROVIDED_MIN") or "").split()
    fill_versions(data, "min", assume_min)
    assume_max = (config.get("ASSUME_PROVIDED_MAX") or "").split()
    fill_versions(data, "max", assume_max)

    func = show_provided
    if options.check:
        func = check_provided

    for p in assume_provided:
        ok = func(p)
        if ok is None:
            continue
        if ok:
            logging.info("%s: OK" % p)
        else:
            logging.info("%s: not OK" % p)
            ret = 1

    return ret
