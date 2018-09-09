"""Servers
==========


"""

import sys
import signal
import time

from multiprocessing import Process, Pipe, Event
from threading import Thread

import trio

from fluiddyn.io.tee import MultiFile
from fluidimage.util import logger, log_memory_usage, cstring

from fluidimage import config_logging


def launch_server(
    topology,
    log_path,
    type_server="multiprocessing",
    sleep_time=0.1,
    logging_level="info",
):

    parent_conn, child_conn = Pipe()

    event_has_to_stop = Event()

    # for testing
    if type_server == "multiprocessing":
        Process_ = Process
    elif type_server == "threading":
        Process_ = Thread
    else:
        raise ValueError

    in_process = Process_ == Process

    process = Process_(
        target=WorkerServerMultiprocessing,
        args=(
            child_conn,
            event_has_to_stop,
            type(topology),
            topology.params,
            sleep_time,
            in_process,
            log_path,
            logging_level,
        ),
    )
    process.daemon = True

    process.start()
    worker = WorkerMultiprocessing(parent_conn, event_has_to_stop, process)

    return worker


class Worker:
    def __init__(self, conn, event_has_to_stop, process):
        self.conn = conn
        self.event_has_to_stop = event_has_to_stop
        self.process = process
        self.nb_items_to_process = 0
        self.is_available = True
        self.is_unoccupied = True

    def well_done_thanks(self):
        self.nb_items_to_process -= 1
        if self.nb_items_to_process == 0:
            self.is_unoccupied = True

    def terminate(self):
        raise NotImplementedError


class WorkerMultiprocessing(Worker):
    def send_job(self, obj):
        self.is_unoccupied = False
        self.nb_items_to_process += 1
        self.send(obj)

    def send(self, obj):
        self.conn.send(obj)

    def new_pipe(self):
        return Pipe()

    def terminate(self):
        # self.conn.send("__terminate__")
        self.event_has_to_stop.set()
        if hasattr(self.process, "terminate"):
            self.process.terminate()


class WorkerServer:
    def __init__(self, sleep_time=0.01):
        self.sleep_time = sleep_time
        self.to_be_processed = []
        self.to_be_resent = []
        self._has_to_continue = True

        trio.run(self._start_async)

    async def _start_async(self):
        async with trio.open_nursery() as self.nursery:
            self.nursery.start_soon(self.check_event_has_to_stop)
            self.nursery.start_soon(self.receive)
            self.nursery.start_soon(self.launch_works)
            self.nursery.start_soon(self.send)

    async def receive(self):
        raise NotImplementedError

    async def send(self):
        raise NotImplementedError

    async def launch_works(self):
        raise NotImplementedError


class WorkerServerMultiprocessing(WorkerServer):
    def __init__(
        self,
        conn,
        event_has_to_stop,
        topology_cls,
        params,
        sleep_time,
        in_process,
        log_path,
        logging_level,
    ):

        if in_process:

            def signal_handler(sig, frame):
                self._has_to_continue = False
                self.nursery.cancel_scope.cancel()

            signal.signal(signal.SIGINT, signal_handler)

        self.conn = conn
        self.event_has_to_stop = event_has_to_stop
        self.topology = topology_cls(params)

        self._log_file = open(log_path, "w")

        stdout = sys.stdout
        if isinstance(stdout, MultiFile):
            stdout = sys.__stdout__

        stderr = sys.stderr
        if isinstance(stderr, MultiFile):
            stderr = sys.__stderr__

        sys.stdout = MultiFile([stdout, self._log_file])
        sys.stderr = MultiFile([stderr, self._log_file])

        if logging_level:
            for handler in logger.handlers:
                logger.removeHandler(handler)

            config_logging(logging_level, file=sys.stdout)

        super().__init__(sleep_time=sleep_time)

    async def check_event_has_to_stop(self):
        while self._has_to_continue:
            if self.event_has_to_stop.is_set():
                self._has_to_continue = False
                break
            await trio.sleep(self.sleep_time)

    async def receive(self):
        while self._has_to_continue:
            ret = await trio.run_sync_in_worker_thread(self.conn.recv)
            logger.debug(f"receive: {ret}")
            if isinstance(ret, tuple) and ret[0] == "__t_start__":
                self.t_start = ret[1]
            else:
                self.to_be_processed.append(ret)

    async def launch_works(self):
        while self._has_to_continue:
            while not self.to_be_processed:
                await trio.sleep(self.sleep_time)

            work_name, key, obj, child_conn = self.to_be_processed.pop(0)
            work = self.topology.works_dict[work_name]

            def do_the_job(work, obj):
                return work.func_or_cls(obj)

            t_start = time.time()

            log_memory_usage(
                f"{time.time() - self.t_start:.2f} s. Launch work "
                + work.name.replace(" ", "_")
                + f" ({key}). mem usage"
            )
            try:
                result = await trio.run_sync_in_worker_thread(
                    do_the_job, work, obj
                )
            except Exception as error:
                logger.error(
                    cstring(
                        "error during work "
                        f"{work.name.replace(' ', '_')} ({key})",
                        color="FAIL",
                    )
                )
                result = error
            else:
                logger.info(
                    f"work {work.name.replace(' ', '_')} ({key}) "
                    f"done in {time.time() - t_start:.3f} s"
                )

            self.to_be_resent.append((work_name, key, result, child_conn))

    async def send(self):
        while self._has_to_continue:
            while not self.to_be_resent:
                await trio.sleep(self.sleep_time)
            work_name, key, result, child_conn = self.to_be_resent.pop(0)

            logger.debug(f"send {work_name}, {key}")
            await trio.run_sync_in_worker_thread(
                child_conn.send, (work_name, key, result)
            )
