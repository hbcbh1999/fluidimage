"""Executor async/await
=======================

This executer uses await/async with trio library to put topology tasks in
concurrency.

A single executor (in one process) is created.  If CPU bounded tasks are limited
by the Python GIL, the threads won't use at the same time the CPU.

This means that the work will run on one CPU at a time, except if the topology
uses compiled code releasing the GIL. In this case, the GIL can be bypassed and
computation can use many CPU at a time.

.. autoclass:: ExecutorAsync
   :members:
   :private-members:

"""

import time
from collections import OrderedDict
import signal

import trio

from fluidimage.util import logger, log_memory_usage

from .base import ExecutorBase


class ExecutorAsync(ExecutorBase):
    """Executor async/await.

    The work in performed in a single process.

    Parameters
    ----------

    nb_max_workers : None, int

      Limits the numbers of workers working in the same time.

    nb_items_queue_max : None, int

      Limits the numbers of items that can be in a output_queue.

    sleep_time : None, float

      Defines the waiting time (from trio.sleep) of a function. Functions await
      "trio.sleep" when they have done a work on an item, and when there is
      nothing in there input_queue.

    """

    def __init__(
        self,
        topology,
        path_dir_result,
        nb_max_workers=None,
        nb_items_queue_max=None,
        sleep_time=0.01,
        logging_level="info",
    ):
        super().__init__(
            topology,
            path_dir_result,
            nb_max_workers,
            nb_items_queue_max,
            logging_level=logging_level,
        )

        self.nb_working_workers_cpu = 0
        self.nb_working_workers_io = 0

        # Executor parameters
        self.sleep_time = sleep_time

        # fonction containers
        self.async_funcs = OrderedDict()
        self.funcs = OrderedDict()

        # Functions definition
        self.define_functions()

        def signal_handler(sig, frame):
            logger.info("Ctrl+C signal received...")
            self._has_to_stop = True
            self.nursery.cancel_scope.cancel()
            # it seems that we don't need to raise the exception
            # raise KeyboardInterrupt

        signal.signal(signal.SIGINT, signal_handler)

    def compute(self):
        """Compute the whole topology.

        Begin by executing one shot jobs, then execute multiple shots jobs
        implemented as async functions.  Warning, one shot jobs must be ancestors
        of multiple shots jobs in the topology.

        """

        self._init_compute()
        self.exec_one_shot_works()
        trio.run(self.start_async_works)
        self._finalize_compute()

    async def start_async_works(self):
        """Create a trio nursery and start all async functions.

        """
        async with trio.open_nursery() as self.nursery:
            for af in self.async_funcs.values():
                self.nursery.start_soon(af)

            self.nursery.start_soon(self.update_has_to_stop)

    def define_functions(self):
        """Define sync and async functions.

        Define sync ("one shot" functions) and async functions (multiple shot
        functions), and store them in `self.async_funcs`.

        The behavior of the executor is mostly defined here.  To sum up : Each
        "multiple shot" waits for an items to be available in there input_queue
        and process the items as soon as they are available.

        """
        for w in self.works:

            # global functions
            if w.kind is not None and "global" in w.kind:

                async def func(work=w):
                    item_number = 1
                    while True:
                        while len(work.output_queue) > self.nb_items_queue_max:
                            await trio.sleep(self.sleep_time)
                        t_start = time.time()
                        while not work.func_or_cls(
                            work.input_queue, work.output_queue
                        ):
                            if self._has_to_stop:
                                return
                            await trio.sleep(self.sleep_time)
                            t_start = time.time()
                        item_number += 1
                        log_memory_usage(
                            "{:.2f} s. ".format(time.time() - self.t_start)
                            + "Launch work "
                            + work.name.replace(" ", "_")
                            + " ({}). mem usage".format(item_number)
                        )
                        logger.info(
                            "work {} ({}) done in {:.3f} s".format(
                                work.name.replace(" ", "_"),
                                "item" + str(item_number),
                                time.time() - t_start,
                            )
                        )
                        await trio.sleep(self.sleep_time)

            # I/O
            elif w.kind is not None and ("io" in w.kind or "io" == w.kind):
                if w.output_queue is not None:
                    func = self.def_async_func_work_io_with_output_queue(w)
                else:
                    func = self.def_async_func_work_io_without_output_queue(w)

            # CPU-bounded work with output_queue
            elif w.output_queue is not None:
                func = self.def_async_func_work_cpu_with_output_queue(w)

            # CPU-bounded work without output_queue
            else:
                func = self.def_async_func_work_cpu_without_output_queue(w)

            self.async_funcs[w.name] = func

    def def_async_func_work_io_with_output_queue(self, work):
        async def func(work=work):
            while True:
                while (
                    not work.input_queue
                    or self.nb_working_workers_io >= self.nb_max_workers
                    or len(work.output_queue) >= self.nb_items_queue_max
                ):
                    if self._has_to_stop:
                        return
                    await trio.sleep(self.sleep_time)
                key, obj = work.input_queue.pop_first_item()
                self.nursery.start_soon(self.async_run_work_io, work, key, obj)
                await trio.sleep(self.sleep_time)

        return func

    def def_async_func_work_io_without_output_queue(self, work):
        async def func(work=work):
            while True:
                while (
                    not work.input_queue
                    or self.nb_working_workers_io >= self.nb_max_workers
                ):
                    if self._has_to_stop:
                        return
                    await trio.sleep(self.sleep_time)
                key, obj = work.input_queue.pop_first_item()
                self.nursery.start_soon(self.async_run_work_io, work, key, obj)
                await trio.sleep(self.sleep_time)

        return func

    def def_async_func_work_cpu_with_output_queue(self, work):
        async def func(work=work):
            while True:
                while (
                    not work.input_queue
                    or self.nb_working_workers_cpu >= self.nb_max_workers
                    or len(work.output_queue) >= self.nb_items_queue_max
                ):
                    if self._has_to_stop:
                        return
                    await trio.sleep(self.sleep_time)
                key, obj = work.input_queue.pop_first_item()
                self.nursery.start_soon(self.async_run_work_cpu, work, key, obj)
                await trio.sleep(self.sleep_time)

        return func

    def def_async_func_work_cpu_without_output_queue(self, work):
        async def func(work=work):
            while True:
                while (
                    not work.input_queue
                    or self.nb_working_workers_cpu >= self.nb_max_workers
                ):
                    if self._has_to_stop:
                        return
                    await trio.sleep(self.sleep_time)
                key, obj = work.input_queue.pop_first_item()
                self.nursery.start_soon(self.async_run_work_cpu, work, key, obj)
                await trio.sleep(self.sleep_time)

        return func

    async def async_run_work_io(self, work, key, obj):
        """Is destined to be started with a "trio.start_soon".

        Executes the work on an item (key, obj), and add the result on
        work.output_queue.

        Parameters
        ----------

        work :

          A work from the topology

        key : hashable

          The key of the dictionnary item to be process

        obj : object

          The value of the dictionnary item to be process

        """
        t_start = time.time()
        log_memory_usage(
            "{:.2f} s. ".format(time.time() - self.t_start)
            + "Launch work "
            + work.name.replace(" ", "_")
            + " ({}). mem usage".format(key)
        )
        self.nb_working_workers_io += 1
        ret = await trio.run_sync_in_worker_thread(work.func_or_cls, obj)
        if work.output_queue is not None:
            work.output_queue[key] = ret
        self.nb_working_workers_io -= 1
        logger.info(
            "work {} ({}) done in {:.3f} s".format(
                work.name.replace(" ", "_"), key, time.time() - t_start
            )
        )

    async def async_run_work_cpu(self, work, key, obj):
        """Is destined to be started with a "trio.start_soon".

        Executes the work on an item (key, obj), and add the result on
        work.output_queue.

        Parameters
        ----------

        work :

          A work from the topology

        key : hashable

          The key of the dictionnary item to be process

        obj : object

          The value of the dictionnary item to be process

        """
        t_start = time.time()
        log_memory_usage(
            f"{time.time() - self.t_start:.2f} s. Launch work "
            + work.name.replace(" ", "_")
            + f" ({key}). mem usage"
        )
        self.nb_working_workers_cpu += 1
        ret = await trio.run_sync_in_worker_thread(work.func_or_cls, obj)
        if work.output_queue is not None:
            work.output_queue[key] = ret
        self.nb_working_workers_cpu -= 1
        logger.info(
            f"work {work.name.replace(' ', '_')} ({key}) "
            f"done in {time.time() - t_start:.3f} s"
        )

    async def update_has_to_stop(self):
        """Work has to stop flag. Check if all works has been done.

        Return True if there are no workers in working and if there is no items in
        all queues.

        """

        while not self._has_to_stop:

            result = (
                (not any([len(queue) != 0 for queue in self.topology.queues]))
                and self.nb_working_workers_cpu == 0
                and self.nb_working_workers_io == 0
            )

            if result:
                self._has_to_stop = True
                logger.debug(f"has_to_stop!")

            if self.logging_level == "debug":
                logger.debug(f"self.topology.queues: {self.topology.queues}")
                logger.debug(
                    f"self.nb_working_workers_cpu: {self.nb_working_workers_cpu}"
                )
                logger.debug(
                    f"self.nb_working_workers_io: {self.nb_working_workers_io}"
                )

            await trio.sleep(self.sleep_time)
