import sys
import os

from .compat import dup_cloexec, open_cloexec

__all__ = ["log", "warn", "info", "set_task_context", "unset_task_context"]

# We build our own logging infrastructure for a few reasons:
#
# (1) We must use stdout (and/or stderr) as our communication channel,
# since (a) lots of existing python code does logging with simple
# print statements (b) when we're in task context, the stdout and
# stderr file descriptors point to the per-task log file so that any
# output from subprocesses go there - if we also write something from
# within python, that should end up in the same place.
#
# (2) Something like Python's stdlib logging module does both too much
# and too little, plus it's extremely hard to figure out what it
# actually does.
#
# (3) Our log messages don't necessarily fall into a strict linear
# hierarchy corresponding to the syslog levels (debug, info, warning
# etc.).
#
# (4) We can have much more fine-grained debugging. For example,
# setting __debug = True in some recipe could cause us to write all
# debug messages done in the context of a task belonging to that
# recipe.


# Create backups of the original stdio fds. When returning from task
# context, we'll use these, and this also makes it possible to write
# messages to the terminal from any context.
orig_stdin_fd = dup_cloexec(0)
orig_stdout_fd = dup_cloexec(1)
orig_stderr_fd = dup_cloexec(2)
# Open /dev/null for reading once; this will be used as stdin when switching to task context.
devnull = open_cloexec("/dev/null", os.O_RDONLY)
# For bookkeeping and sanity checks
current_task = None

# There's an ancient commit in oebakery changing sys.stdout and
# sys.stderr to unbuffered mode. I believe that's an unnecessary
# pessimization. Having sys.stderr unbuffered is fine (that's usual
# unix semantics), but we can at least make stdout
# line-buffered. Careful: The code in oebakery got away with doing
#
#   sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 0)
#
# However, if we now do a very similar-looking
#
#   sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
#
# things will go wrong! The reason is that the original sys.stdout
# object has two references; the other is known as
# sys.__stdout__. Hence that object is kept alive even after the code
# in oebakery runs. But the new sys.stdout object only has that single
# reference, so if we replace that object ("bind another object to the
# sys.stdout name"), the object created by oebakery will be destroyed,
# and for a Python stream, that implies closing the associated file
# descriptor. So we either have to keep the oebakery object alive by
# stashing a dummy reference, or explicitly dispose of it, recreating
# the original fd from orig_stdout_fd.
#
# For now, skip this; I just wanted a place to put these comments.
if False:
    please_dont_close_fd1_I_need_it = sys.stdout
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)


# When we change the file descriptor from underneath a Python file
# object, we have to make sure to flush any pending output before the
# replacement; otherwise a later flush would make that output appear
# in the wrong place.
def _flush_stdio():
    sys.stdout.flush()
    sys.stderr.flush()
    
def set_task_context(task):
    global current_task
    assert(current_task is None)
    current_task = task
    _flush_stdio()
    os.dup2(devnull, 0)
    os.dup2(task.logfilefd, 1)
    os.dup2(task.logfilefd, 2)

def unset_task_context(task):
    global current_task
    _flush_stdio()
    os.dup2(orig_stdin_fd, 0)
    os.dup2(orig_stdout_fd, 1)
    os.dup2(orig_stderr_fd, 2)
    # Do the assertion after we've pointed stderr back at the terminal
    assert(current_task is task)
    current_task = None

# Stuff we could implement:
#
# timestamps on every line/on lines going to the terminal/on lines
# going to task logs/on lines where explicitly requested. Can easily be
# configured with a usersettable strftime string.
#
# when printing to the terminal, check if current_task is set; if so,
# preceed the message with the task name.
#
# 


# I really hate that I often end up writing print("%s %d", foo, bar)
# when it should have been print("%s %d" % (foo, bar)), especially
# when it's a dummy print statement I've inserted for debugging
# purposes. It's not really hard to make the log functions DTRT: All
# optional arguments must be given via keywords; any positional
# arguments are assumed to be for doing formatting. If there are no
# positional arguments, either the caller did the formatting himself
# or there simply is nothing to do.

def _log(msg, **kwargs):
    # Intelligent newline. If the caller doesn't provide one and
    # doesn't explicitly suppress this, we append one.
    if not msg.endswith("\n") and not kwargs.get("no_newline"):
        msg += "\n"
    # For now, this doesn't do much other than a print statement would
    # have done.
    sys.stdout.write(msg)
    
# We'll create convenience wrappers for this.
def log(fmt, *args, **kwargs):
    msg = fmt
    if args:
        try:
            msg = fmt % args
        except TypeError as e:
            tb = traceback.format_exc()
            # This should really just call log itself with a
            # write_to_terminal arg, but we're not there yet.
            os.write(orig_stderr_fd, "Log format string bug: fmt = %s" % fmt)
            os.write(orig_stderr_fd, tb)
            # Fall through and just use the format string as log
            # message. The non-format parts might still contain useful
            # info.
    _log(msg, **kwargs)

def info(fmt, *args, **kwargs):
    kwargs['level'] = INFO
    log(fmt, *args, **kwargs)
def warn(fmt, *args, **kwargs):
    kwargs['level'] = WARN
    log(fmt, *args, **kwargs)
# etc. etc.
