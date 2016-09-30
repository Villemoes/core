import oebakery
from oebakery import die, err, warn, info, debug
from oelite import *
from recipe import OEliteRecipe
from runq import OEliteRunQueue
import oelite.meta
import oelite.util
import oelite.arch
import oelite.parse
import oelite.task
import oelite.item
from oelite.parse import *
from oelite.cookbook import CookBook

import oelite.fetch

import bb.utils

import sys
import os
import glob
import shutil
import datetime
import hashlib
import logging
import time

class OEliteOven:
    def __init__(self, baker, capacity=None):
        if capacity is None:
            pmake = baker.config.get("PARALLEL_MAKE")
            if pmake is None or pmake == "":
                capacity = 3
            else:
                capacity = int(pmake.replace("-j", "")) + 2
        self.capacity = capacity
        self.baker = baker
        self.starttime = dict()
        self.completed_tasks = []
        self.failed_tasks = []
        self.total = baker.runq.number_of_tasks_to_build()
        self.count = 0
        self.task_stat = dict()
        self.stdout_isatty = os.isatty(sys.stdout.fileno())

    def currently_baking(self):
        return self.starttime.keys()

    def update_task_stat(self, task, delta):
        try:
            stat = self.task_stat[task.name]
        except KeyError:
            stat = self.task_stat[task.name] = oelite.profiling.SimpleStats()
        stat.append(delta)

    def add(self, task):
        self.capacity -= task.weight
        self.starttime[task] = oelite.util.now()

    def remove(self, task):
        now = oelite.util.now()
        delta = (now - self.starttime[task]).total_seconds()
        del self.starttime[task]
        self.capacity += task.weight
        self.update_task_stat(task, delta)
        return delta

    def start(self, task):
        self.count += 1
        info("%s started - %d / %d "%(task, self.count, self.total))
        task.build_started()

        self.add(task)
        task.start()

    def wait_task(self, poll, task):
        """Wait for a specific task to finish baking. Returns pair (result,
        delta), or None in case poll=True and the task is not yet
        done.

        """
        assert(task in self.starttime)
        result = task.wait(poll)
        if result is None:
            return None
        delta = self.remove(task)
        task.task_time = delta

        task.recipe.remaining_tasks -= 1
        if result:
            info("%s finished - %s s" % (task, delta))
            task.build_done(self.baker.runq.get_task_buildhash(task))
            self.baker.runq.mark_done(task)
            self.completed_tasks.append(task)
        else:
            err("%s failed - %s s" % (task, delta))
            self.failed_tasks.append(task)
            task.build_failed()

        return (task, result, delta)

    def wait_any(self, poll):
        """Wait for any task currently in the oven to finish. Returns triple
        (task, result, time), or None.

        """
        if not poll and not self.starttime:
            raise Exception("nothing in the oven, so you'd wait forever...")
        tasks = self.starttime.keys()
        if not poll and len(tasks) == 1:
            t = tasks[0]
            if self.stdout_isatty:
                now = oelite.util.now()
                info("waiting for %s (started %6.2f ago) to finish" % (t, (now-self.starttime[t]).total_seconds()))
            return self.wait_task(False, t)
        tasks.sort(key=lambda t: self.starttime[t])
        i = 0
        while True:
            for t in tasks:
                result = self.wait_task(True, t)
                if result is not None:
                    return result
            if poll:
                break
            i += 1
            if i == 4 and self.stdout_isatty:
                info("waiting for any of these to finish:")
                now = oelite.util.now()
                for t in tasks:
                    info("  %-40s started %6.2f s ago" % (t, (now-self.starttime[t]).total_seconds()))
            time.sleep(0.1)
        return None

    def wait_all(self, poll):
        """Do wait_task once for every task currently in the oven once. With
        poll=False, this amounts to waiting for every current task to
        finish. Returns number of tasks succesfully waited for.
        """
        tasks = self.starttime.keys()
        tasks.sort(key=lambda t: self.starttime[t])
        ret = 0
        for t in tasks:
            if self.wait_task(poll, t):
                ret += 1
        return ret
