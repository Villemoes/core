import oebakery
from oebakery import die, err, warn, info, debug
import oelite.meta
import oelite.recipe
from oelite.dbutil import *
import oelite.function
import oelite.util
from oelite.compat import open_cloexec

import sys
import os
import warnings
import shutil
import re

def task_name(name):
    if name.startswith("do_"):
        return name
    return "do_" + name


class OEliteTask:

    TASKFUNC_RE = re.compile(r"^do_[a-z]+")

    def __init__(self, id, recipe, name, nostamp, cookbook):
        self.id = id
        self.recipe = cookbook.get_recipe(id=recipe)
        self.name = name
        self.nostamp = nostamp
        self.cookbook = cookbook
        self.debug = self.cookbook.debug
        self._meta = None
        self.result = None
        return

    def __str__(self):
        return "%s:%s"%(self.recipe, self.name)

    def get_weight(self, meta):
        if not self.name == "do_compile":
            return 1
        pmake = meta.get("PARALLEL_MAKE")
        if pmake is None or pmake == "":
            return 1
        return int(pmake.replace("-j", ""))

    def get_parents(self):
        parents = flatten_single_column_rows(self.cookbook.dbc.execute(
            "SELECT parent FROM task_parent WHERE recipe=? AND task=?",
            (self.recipe.id, self.name,)))
        if not parents:
            return []
        return self.cookbook.get_tasks(recipe=self.recipe, name=parents)

    def get_deptasks(self, deptype):
        return flatten_single_column_rows(self.cookbook.dbc.execute(
            "SELECT deptask FROM task_deptask "
            "WHERE deptype=? "
            "AND task=?", (deptype,self.id)))

    def get_recdeptasks(self, deptype):
        return flatten_single_column_rows(self.cookbook.dbc.execute(
            "SELECT recdeptask FROM task_recdeptask "
            "WHERE deptype=? "
            "AND task=?", (deptype,self.id)))


    def stampfile_path(self):
        try:
            return self._stampfile
        except AttributeError:
            stampdir = self.recipe.get("STAMPDIR")
            self._stampfile = (stampdir, os.path.join(stampdir, self.name))
        return self._stampfile


    # return (mtime, hash) from stamp file
    def read_stamp(self):
        stampfile = self.stampfile_path()[1]
        if not os.path.exists(stampfile):
            return (None, None)
        if not os.path.isfile(stampfile):
            die("bad hash file: %s"%(stampfile))
        if os.path.getsize(stampfile) == 0:
            return (None, None)
        mtime = os.stat(stampfile).st_mtime
        with open(stampfile, "r") as stampfile:
            tmphash = stampfile.read()
        return (mtime, tmphash)


    def build_started(self):
        if self.nostamp:
            return
        (stampdir, stampfile) = self.stampfile_path()
        if not os.path.exists(stampdir):
            os.makedirs(stampdir)
        open(stampfile, "w").close()
        return


    def build_done(self, buildhash):
        if self.nostamp:
            return
        (stampdir, stampfile) = self.stampfile_path()
        if not os.path.exists(stampdir):
            os.makedirs(stampdir)
        with open(stampfile, "w") as _stampfile:
            _stampfile.write(buildhash)
        return


    def build_failed(self):
        return


    @oelite.profiling.profile_calls
    def prepare(self):
        meta = self.meta()
        self.weight = self.get_weight(meta)

        buildhash = self.cookbook.baker.runq.get_task_buildhash(self)
        debug("buildhash=%s"%(repr(buildhash)))
        meta.set("TASK_BUILDHASH", buildhash)

        @oelite.profiling.profile_calls
        def prepare_stage(deptype):
            stage = {}
            recdepends = []
            get_package_filename = self.cookbook.baker.runq.get_package_filename
            packages = self.cookbook.baker.runq.get_depend_packages(
                self, deptype) or []
            for package in packages:
                package = self.cookbook.get_package(id=package)
                filename = get_package_filename(package)
                if not filename in stage:
                    stage[filename] = package
            return stage

        meta["__stage"] = prepare_stage("DEPENDS")
        meta["__rstage"] = prepare_stage("RDEPENDS")
        meta["__fstage"] = prepare_stage("FDEPENDS")

    @oelite.profiling.profile_calls
    def filter_meta(self):
        meta = self._meta
        assert(meta is not None)
        # Filter meta-data, enforcing restrictions on which tasks to
        # emit vars to and not including other task functions.
        emit_prefixes = (meta.get("META_EMIT_PREFIX") or "").split()
        def emit_prefix_pair(s):
            task, prefix = s.split(":", 1)
            if task:
                task = "do_" + task
            return (task, prefix)
        # To avoid looping over the entire ~20 element list of pairs
        # for every variable, split that list according to the first
        # character of the prefix, and fetch the appropriate list
        # based on var[0].
        emit_prefix_table = {}
        for s in emit_prefixes:
            p = emit_prefix_pair(s)
            c = p[1][0]
            if c in emit_prefix_table:
                emit_prefix_table[c].append(p)
            else:
                emit_prefix_table[c] = [p]
        for var in meta.keys():
            emit_flag = meta.get_flag(var, "emit")
            emit = (emit_flag or "").split()
            taskfunc_match = self.TASKFUNC_RE.match(var)
            if taskfunc_match:
                emit.append(taskfunc_match.group(0))
            for emit_task, emit_prefix in emit_prefix_table.get(var[0], []):
                if not var.startswith(emit_prefix):
                    continue
                if emit_task == "":
                    if emit_flag is None:
                        emit_flag = ""
                    continue
                emit.append(emit_task)
            if (emit or emit_flag == "") and not self.name in emit:
                del meta[var]
                continue

    @oelite.profiling.profile_calls
    def meta(self):
        if self._meta is not None:
            return self._meta
        self.recipe.meta._fill_expand_cache()
        self._meta = self.recipe.meta.copy()
        self.filter_meta()
        self._meta.trim()
        return self._meta

    def prepare_context(self):
        meta = self.meta()
        self.function = meta.get_function(self.name)
        self.do_cleandirs()
        self.cwd = self.do_dirs() or meta.get("B")
        self.stdin = open_cloexec("/dev/null", os.O_RDONLY)
        self.logfn = "%s/%s.%s.log"%(self.function.tmpdir, self.name, meta.get("DATETIME"))
        self.logsymlink = "%s/%s.log"%(self.function.tmpdir, self.name)
        oelite.util.makedirs(os.path.dirname(self.logfn))
        try:
            self.logfilefd = open_cloexec(self.logfn, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0666)
        except OSError:
            print "Opening log file failed: %s"%(self.logfn)
            raise

        if os.path.exists(self.logsymlink) or os.path.islink(self.logsymlink):
            os.remove(self.logsymlink)
        os.symlink(os.path.basename(self.logfn), self.logsymlink)

    def save_context(self):
        self.saved_stdio = oelite.util.StdioSaver()

    def apply_context(self):
        os.dup2(self.stdin, sys.stdin.fileno())
        sys.stdout.flush()
        os.dup2(self.logfilefd, sys.stdout.fileno())
        sys.stderr.flush()
        os.dup2(self.logfilefd, sys.stderr.fileno())

    def restore_context(self):
        self.saved_stdio.restore(True)

    def cleanup_context(self):
        os.close(self.stdin)
        os.close(self.logfilefd)
        if os.path.exists(self.logfn):
            if os.path.getsize(self.logfn) == 0:
                os.remove(self.logsymlink)
                os.remove(self.logfn) # prune empty logfiles
            elif self.debug:
                # cat the log-file to stdout
                print '----- %s ----' % self.logfn
                with open(self.logfn) as f:
                    shutil.copyfileobj(f, sys.stdout)
                print '----- %s ----' % self.logfn

    def _start(self):
        self.prepare_context()

        self.save_context()
        self.apply_context()

        try:
            prefunc_start = oelite.util.now()
            for prefunc in self.get_prefuncs():
                print "running prefunc", prefunc
                self.do_cleandirs(prefunc)
                wd = self.do_dirs(prefunc)
                if not prefunc.run(wd or self.cwd):
                    return False
            self.func_start = oelite.util.now()
            self.prefunc_time = self.func_start - prefunc_start
            try:
                # start() doesn't return a value - but it may throw an
                # exception. For, I think, mostly historical reasons,
                # we catch one specific exception and treat that
                # essentially as if the last prefunc failed, and let
                # others trickle up. For now, keep that oddity, since
                # it doesn't complicate our wait() method.
                self.function.start(self.cwd)
            except oebakery.FatalError:
                return False
        finally:
            # Cleanup stdin, stdout and stderr redirection
            self.restore_context()
        return None

    def start(self):
        oelite.util.stracehack("==>%s" % self.name)
        self.result = self._start()
        oelite.util.stracehack("<==%s" % self.name)

    def wait(self, poll=False):
        oelite.util.stracehack("==>%s" % self.name)
        if self.result is not None:
            # Something bad happened in start
            self.cleanup_context()
            oelite.util.stracehack("<==%s" % self.name)
            return self.result

        self.save_context()
        self.apply_context()

        try:
            # Do the actual wait
            self.result = self.function.wait(poll)
            assert(self.result in (True, False, None))
            assert(poll or self.result is not None)
            # This may have returned None, in case we were just
            # polling for completion, or False, in case the function
            # failed. In either case, we shouldn't run the
            # postfuncs. Otherwise, we should run them.
            if self.result:
                func_end = oelite.util.now()
                self.func_time = func_end - self.func_start
                for postfunc in self.get_postfuncs():
                    print "running postfunc", postfunc
                    self.do_cleandirs(postfunc)
                    wd = self.do_dirs(postfunc)
                    if not postfunc.run(wd or self.cwd):
                        self.result = False
                        break
                self.postfunc_time = oelite.util.now() - func_end
        finally:
            self.restore_context()

        # If we've gotten an actual True/False answer, it's time to
        # close the log file and clean up our context. The caller
        # shouldn't call wait() once the result has been obtained, so
        # we don't need to make that idempotent.
        if self.result is not None:
            self.cleanup_context()
        oelite.util.stracehack("<==%s" % self.name)
        return self.result


    def run(self):
        self.start()
        return self.wait(False)

    def do_cleandirs(self, name=None):
        if not name:
            name = self.name
        cleandirs = (self.meta().get_flag(name, "cleandirs",
                                          oelite.meta.FULL_EXPANSION))
        if cleandirs:
            for cleandir in cleandirs.split():
                if not os.path.exists(cleandir):
                    continue
                try:
                    #print "cleandir %s"%(cleandir)
                    if os.path.islink(cleandir):
                        os.unlink(cleandir)
                    else:
                        shutil.rmtree(cleandir)
                except Exception, e:
                    err("cleandir %s failed: %s"%(cleandir, e))
                    raise

    def do_dirs(self, name=None):
        if not name:
            name = self.name
        # Create directories and find directory to execute in
        dirs = (self.meta().get_flag(name, "dirs",
                                     oelite.meta.FULL_EXPANSION))
        if dirs:
            dirs = dirs.split()
            for dir in dirs:
                oelite.util.makedirs(dir)
            return dir

    def get_postfuncs(self):
        postfuncs = []
        for name in (self.meta().get_flag(self.name, "postfuncs", 1)
                     or "").split():
            postfuncs.append(self.meta().get_function(name))
        return postfuncs

    def get_prefuncs(self):
        prefuncs = []
        for name in (self.meta().get_flag(self.name, "prefuncs", 1)
                     or "").split():
            prefuncs.append(self.meta().get_function(name))
        return prefuncs
