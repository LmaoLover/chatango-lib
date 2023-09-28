import sys
import inspect
import logging
import traceback
import asyncio

logger = logging.getLogger(__name__)


class EventHandler:
    async def on_event(self, event: str, *args, **kwargs):
        if "ping" in event or "pong" in event:
            return
        if len(args) == 0:
            args_section = ""
        elif len(args) == 1:
            args_section = args[0]
        else:
            args_section = repr(args)
        kwargs_section = "" if not kwargs else repr(kwargs)
        logger.debug(f"EVENT {event} {args_section} {kwargs_section}")

    async def _call_event(self, event: str, *args, **kwargs):
        attr = f"on_{event}"
        await self.on_event(event, *args, **kwargs)
        if hasattr(self, attr):
            asyncio.create_task(getattr(self, attr)(*args, **kwargs))

    def event(self, func, name=None):
        assert inspect.iscoroutinefunction(func)
        if name is None:
            event_name = func.__name__
        else:
            event_name = name
        setattr(self, event_name, func)


class CommandHandler:
    """
    Internal method to send a command using the protocol of the
    subclass (websocket, tcp, etc.)
    """
    async def _send_command(self, *args, **kwargs):
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
            except:
                logger.error(f"Error while handling command {action}")
                traceback.print_exc(file=sys.stderr)
        else:
            logger.error(f"Unhandled received command {action}")
