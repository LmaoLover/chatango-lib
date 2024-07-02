import sys
import asyncio
import logging
import traceback
from collections.abc import Iterable
from typing import Coroutine


logger = logging.getLogger(__name__)

"""
Base class with helpers for asyncio task management. This allows chat rooms
and other objects to offer some simple task infrastructure so users don't
have to track and await them manually.  Tasks with errors will be logged for
visibility during development.  You can await complete_tasks which returns
when all tasks are finished, or tasks_forever which never returns.
"""


class TaskHandler:
    """
    All tasks stored and tracked
    """

    @property
    def tasks(self):
        assert self.task_loop
        if not hasattr(self, "_tasks"):
            self._tasks = []
        return self._tasks

    """
    Main task loop which is started automatically and never ends
    """

    @property
    def task_loop(self):
        if not hasattr(self, "_task_loop") or not self._task_loop:
            self._task_loop = asyncio.create_task(self.tasks_forever())
        return self._task_loop

    """
    Add and run a new task
    """

    def add_task(self, coro: Coroutine):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    """
    Convenience wrapper for sleep before a task
    """

    async def _delayed_task(self, delay_time, coro: Coroutine):
        await asyncio.sleep(delay_time)
        await coro

    """
    Add a task that will start after some time
    """

    def add_delayed_task(self, delay_time, coro: Coroutine):
        self.add_task(self._delayed_task(delay_time, coro))

    """
    Cancel all remaining tasks
    """

    def cancel_tasks(self):
        for task in self.tasks:
            task.cancel()

    """
    Cancel all tasks including the task loop
    """

    def end_tasks(self):
        self.cancel_tasks()
        self.task_loop.cancel()

    """
    Remove all done tasks, and log any exceptions if present
    """

    def _prune_tasks(self):
        for task in self.tasks:
            if task.done():
                if task.exception():
                    self._on_task_exception(task)
                    # Run as a one-off task in case it throws an exception itself
                    asyncio.create_task(self.on_task_exception(task))
                self.tasks.remove(task)

    """
    Default behavior when a task results in an exception
    """

    def _on_task_exception(self, task: asyncio.Task):
        logger.error(f"Exception in task: {repr(task.get_coro())}")
        task.print_stack(file=sys.stderr)

    """
    Callback for custom behavior on task errors
    """

    async def on_task_exception(self, task: asyncio.Task):
        pass

    """
    Infinite loop to keep task maintenance for the life of object
    """

    async def tasks_forever(self):
        while True:
            self._prune_tasks()
            await asyncio.sleep(1)

    """
    Loop to watch tasks and exit when all are completed
    """

    async def complete_tasks(self):
        while self.tasks:
            self._prune_tasks()
            await asyncio.gather(*self.tasks)
            await asyncio.sleep(0.1)


"""
Base class which allows generating events for itself and other listeners.
In general this allows a chat room to generate events, and customs bots
can implement "on_event" style callbacks to add custom behaviors, either
through a subclass or by a listener class.  For listeners, this object is
passed as the first parameter to the callback.

 Event:
   room.call_event("message", msg_obj)
 Callbacks:
   room.on_message(msg_obj)
   room.on_event("message", msg_obj)
   listener.on_message(room, msg_obj)
   listener.on_event(room, "message", msg_obj)

"""


class EventHandler(TaskHandler):
    """
    All objects listening here for events
    """

    @property
    def listeners(self):
        if not hasattr(self, "_listeners"):
            self._listeners = set()
        return self._listeners

    """
    Add a listener for our events
    """

    def add_listener(self, listener):
        self.listeners.add(listener)

    """
    Trigger an event, which looks for callback methods on this object,
    and any listening objects.
    """

    def call_event(self, event: str, *args, **kwargs):
        attr = f"on_{event}"
        self._log_event(event, *args, **kwargs)
        # Call a generic event handler for all events
        if hasattr(self, "on_event"):
            self.add_task(getattr(self, "on_event")(event, *args, **kwargs))
        # Call the event handler on self
        if hasattr(self, attr):
            self.add_task(getattr(self, attr)(*args, **kwargs))
        # Call the same handlers on any listeners, passing self as first arg
        if self.listeners and isinstance(self.listeners, Iterable):
            for listener in self.listeners:
                if isinstance(listener, TaskHandler):
                    target = listener
                else:
                    target = self
                if hasattr(listener, "on_event"):
                    target.add_task(
                        getattr(listener, "on_event")(self, event, *args, **kwargs)
                    )
                if hasattr(listener, attr):
                    target.add_task(getattr(listener, attr)(self, *args, **kwargs))

    """
    Debug log all events
    """

    def _log_event(self, event: str, *args, **kwargs):
        if len(args) == 0:
            args_section = ""
        elif len(args) == 1:
            args_section = args[0]
        else:
            args_section = repr(args)
        kwargs_section = "" if not kwargs else repr(kwargs)
        logger.debug(f"EVENT {event} {args_section} {kwargs_section}")


"""
Base class for any socket connection to Chatango. Concrete classes must
provide implementation for _send_command which sends the command out on
the network.

The method _receive_command parses the command format, and will automatically
call a method handler named _rcmd_{action}:

 Command:
   premium:0:12345678

 Method:
   _rcmd_premium
 args:
   ["0", "12345678"]

"""


class CommandHandler:
    """
    Internal method to send a command using the protocol of the
    subclass (websocket, tcp, etc.)
    """

    async def _send_command(self, *args, **kwargs) -> None:
        raise TypeError("CommandHandler child class must implement _send_command")

    """
    Public send method
    """

    async def send_command(self, *args):
        command = ":".join(args)
        logger.debug(f"OUT {command}")
        await self._send_command(command)

    """
    Receive an incoming command and dynamically call a handler
    """

    async def _receive_command(self, command: str):
        if not command:
            return
        logger.debug(f" IN {command}")
        action, *args = command.split(":")
        if hasattr(self, f"_rcmd_{action}"):
            try:
                await getattr(self, f"_rcmd_{action}")(args)
            except Exception as e:
                logger.error(f"Error while handling command {action}")
                traceback.print_exception(e, file=sys.stderr)
        else:
            logger.error(f"Unhandled received command {action}")
