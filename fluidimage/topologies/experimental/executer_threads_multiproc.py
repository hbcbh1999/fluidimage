"""executer_threads_multiproc

An executer with the same methods as the "standard" fluidimage topology,
i.e. using threads for IO bounded tasks and multiprocessing for CPU-bounded tasks.

"""


from time import sleep, time
import gc
import os

import threading
from multiprocessing import Process

try:
    import queue
except ImportError:
    # python 2
    import Queue as queue


from fluiddyn import time_as_str


from ...util.util import cstring, logger, log_memory_usage
from ..waiting_queues.base import (
    WaitingQueueMultiprocessing,
    WaitingQueueThreading,
    WaitingQueueMakeCouple,
    WaitingQueueLoadImage,
    WaitingQueueBase,
)
from fluidimage.topologies.experimental.base import Queue
from fluidimage.topologies.experimental.base import Work
from .executer_base import ExecuterBase
from .nb_workers import nb_max_workers

dt = 0.25  # s
dt_small = 0.02
dt_update = 0.1


class ExecuterThreadsMultiprocs(ExecuterBase):
    def __init__(self, topology):
        super().__init__(topology)
        # we have to create a list of queues (WaitingQueueMultiprocessing,
        # WaitingQueueThreading, WaitingQueueMakeCouple, WaitingQueueLoadImage)
        # from the topology...

        self.queues = []
        # create waiting queues :
        # for q in reversed(topology.queues):
        #         self.queues.append(WaitingQueueBase(name=q.name, work=q.name))

        # implement topologie
        for w in reversed(topology.works):
            self.add_queue(w)



        print("len self.queue :{}".format(len(self.queues)))
        self._has_to_stop = False


    def compute(self, sequential=None, has_to_exit=True):
        """Compute (run all works to be done).

        Parameters
        ----------

        sequential : None

          If bool(sequential) is True, the computations are run in sequential
          (useful for debugging).

        has_to_exit : True

          If bool(has_to_exit) is True and if the computation has to stop
          because of a signal 12 (cluster), a signal 99 is sent at exit.

        """


        for w in self.topology.works:
            print("###WORK### kind = {} name = {} input queue = {}".format(w.kind, w.name, w.input_queue))
            if w.input_queue is None: #First work or no queue before work
                w.func_or_cls(None,w.output_queue)
            elif w.kind is not None and "global" not in w.kind :
                if isinstance(w.func_or_cls, object):
                    key, obj = w.input_queue.queue.popitem()
                    print(key,obj)
                    ret = w.func_or_cls(obj)
                    w.output_queue.queue[key] = ret
                else:
                    queue = w.input_queue.queue
                    w.func_or_cls(queue, w.output_queue)
                    print(w.output_queue.queue)
            else:
                w.func_or_cls(w.input_queue, w.output_queue)
                print(w.output_queue[0].queue)
                print(w.output_queue[1].queue)

        if hasattr(self, "path_output"):
            logger.info("path results:\n" + self.path_output)
            if hasattr(self, "params"):
                tmp_path_params = os.path.join(
                    self.path_output, "params_" + time_as_str() + "_" + str(os.getpid())
                )

                if not os.path.exists(tmp_path_params + ".xml"):
                    path_params = tmp_path_params + ".xml"
                else:
                    i = 1
                    while os.path.exists(tmp_path_params + "_" + str(i) + ".xml"):
                        i += 1
                    path_params = tmp_path_params + "_" + str(i) + ".xml"
                self.params._save_as_xml(path_params)

        self.t_start = time()

        log_memory_usage(time_as_str(2) + ": start compute. mem usage")

        self.nb_workers_cpu = 0
        self.nb_workers_io = 0
        workers = []

        class CheckWorksThread(threading.Thread):
            cls_to_be_updated = threading.Thread

            def __init__(self):
                self.has_to_stop = False
                super(CheckWorksThread, self).__init__()
                self.exitcode = None
                self.daemon = True

            def in_time_loop(self):
                t_tmp = time()
                for worker in workers:
                    if (
                        isinstance(worker, self.cls_to_be_updated)
                        and worker.fill_destination()
                    ):
                        workers.remove(worker)
                t_tmp = time() - t_tmp
                if t_tmp > 0.2:
                    logger.info(
                        "update list of workers with fill_destination "
                        "done in {:.3f} s".format(t_tmp)
                    )
                sleep(dt_update)

            def run(self):
                try:
                    while not self.has_to_stop:
                        self.in_time_loop()
                except Exception as e:
                    print("Exception in UpdateThread")
                    self.exitcode = 1
                    self.exception = e

        class CheckWorksProcess(CheckWorksThread):
            cls_to_be_updated = Process

            def in_time_loop(self):
                # weird bug subprocessing py3
                for worker in workers:
                    if not worker.really_started:
                        # print('check if worker has really started.' +
                        #       worker.key)
                        try:
                            worker.really_started = worker.comm_started.get_nowait()
                        except queue.Empty:
                            pass
                        if not worker.really_started and time() - worker.t_start > 10:
                            # bug! The worker does not work. We kill it! :-)
                            logger.error(
                                cstring(
                                    "Mysterious bug multiprocessing: "
                                    "a launched worker has not started. "
                                    "We kill it! ({}, key: {}).".format(
                                        worker.work_name, worker.key
                                    ),
                                    color="FAIL",
                                )
                            )
                            # the case of this worker has been
                            worker.really_started = True
                            worker.terminate()

                super(CheckWorksProcess, self).in_time_loop()

        self.thread_check_works_t = CheckWorksThread()
        self.thread_check_works_t.start()

        self.thread_check_works_p = CheckWorksProcess()
        self.thread_check_works_p.start()

        print(self)
        while not self._has_to_stop and (
            any([not q.is_empty() for q in self.queues]) or len(workers) > 0
        ):

            # debug
            # if logger.level == 10 and \
            #    all([q.is_empty() for q in self.queues]) and len(workers) == 1:
            #     for worker in workers:
            #         try:
            #             is_alive = worker.is_alive()
            #         except AttributeError:
            #             is_alive = None

            #         logger.debug(
            #             str((worker, worker.key, worker.exitcode, is_alive)))

            #         if time() - worker.t_start > 60:
            #             from fluiddyn import ipydebug
            #             ipydebug()

            self.nb_workers = len(workers)

            # slow down this loop...
            sleep(dt_small)
            if self.nb_workers_cpu >= nb_max_workers:
                logger.debug(
                    cstring(
                        ("The workers are saturated: " "{}, sleep {} s").format(
                            self.nb_workers_cpu, dt
                        ),
                        color="WARNING",
                    )
                )
                sleep(dt)

            for q in self.queues:
                if not q.is_empty():
                    logger.debug(q)
                    logger.debug("check_and_act for work: " + repr(q.work))
                    try:
                        new_workers = q.check_and_act(sequential=sequential)
                    except OSError:
                        logger.error(
                            cstring(
                                "Memory full: to free some memory, no more "
                                "computing job will be launched while the last "
                                "(saving) waiting queue is not empty.",
                                color="FAIL",
                            )
                        )
                        log_memory_usage(color="FAIL", mode="error")
                        self._clear_save_queue(workers, sequential)
                        logger.info(
                            cstring(
                                "The last waiting queue has been emptied.", color="FAIL"
                            )
                        )
                        log_memory_usage(color="FAIL", mode="info")
                        continue

                    if new_workers is not None:
                        for worker in new_workers:
                            workers.append(worker)
                    logger.debug("workers: " + repr(workers))

            if self.thread_check_works_t.exitcode:
                raise self.thread_check_works_t.exception

            if self.thread_check_works_p.exitcode:
                raise self.thread_check_works_p.exception

            if len(workers) != self.nb_workers:
                gc.collect()

        if self._has_to_stop:
            logger.info(
                cstring(
                    "Will exist because of signal 12.",
                    "Waiting for all workers to finish...",
                    color="FAIL",
                )
            )
            self._clear_save_queue(workers, sequential)

        self.thread_check_works_t.has_to_stop = True
        self.thread_check_works_p.has_to_stop = True
        self.thread_check_works_t.join()
        self.thread_check_works_p.join()

        # TODO self._print_at_exit(time() - self.t_start)

        log_memory_usage(time_as_str(2) + ": end of `compute`. mem usage")

        if self._has_to_stop and has_to_exit:
            logger.info(cstring("Exit with signal 99.", color="FAIL"))
            exit(99)

    def add_queue(self, work):
        if len(self.queues) is 0:
            destination = None
        else:
            destination = self.queues[-1]
        if work.kind is not None and work.input_queue is not None:
            if "io" in work.kind:
                self.queues.append(
                    WaitingQueueThreading(
                        name=work.input_queue,
                        work_name=work.name,
                        work=work.func_or_cls,
                        destination=destination,
                    )
                )
            else:
                if "global" in work.kind:
                    self.queues.append(
                        WaitingQueueMakeCouple(
                            name=work.input_queue, destination=destination
                        )
                    )
                else:
                    self.queues.append(
                        WaitingQueueMultiprocessing(
                            name=work.input_queue,
                            work=work.func_or_cls,
                            destination=destination,
                        )
                    )
        else:
            self.queues.append(
                WaitingQueueMultiprocessing(
                    name=work.input_queue,
                    work=work.func_or_cls,
                    destination=destination,
                )
            )

    def _clear_save_queue(self, workers, sequential):
        """Clear the last queue (which is often saving) before stopping."""
        q = self.queues[-1]

        idebug = 0
        # if the last queue is a WaitingQueueThreading (saving),
        # it is also emptied.
        while len(workers) > 0 or (
            not q.is_empty() and isinstance(q, WaitingQueueThreading)
        ):

            sleep(0.5)

            if len(workers) == 1 and q.is_empty():
                idebug += 1
                p = workers[0]
                if idebug == 100:
                    print("Issue:", p, p.exitcode)
            # from fluiddyn import ipydebug
            # ipydebug()

            if not q.is_empty() and isinstance(q, WaitingQueueThreading):
                new_workers = q.check_and_act(sequential=sequential)
                if new_workers is not None:
                    for worker in new_workers:
                        workers.append(worker)

    # workers[:] = [w for w in workers
    #               if not w.fill_destination()]
