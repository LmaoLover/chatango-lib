import asyncio
import aiohttp
import inspect
import re
from typing import Optional

from .pm import PM
from .room import Room
from .exceptions import AlreadyConnectedError, NotConnectedError
from .utils import Task, trace


class Client:
    def __init__(
        self,
        aiohttp_session: Optional[aiohttp.ClientSession] = None,
        debug=False,
    ):
        if aiohttp_session is None:
            aiohttp_session = aiohttp.ClientSession(trace_configs=[trace()])

        self.aiohttp_session = aiohttp_session
        self.loop = asyncio.AbstractEventLoop
        self.pm = None
        self.user = None
        self.debug = debug

        self._running = False
        self.silent = 2
        self._rooms = {}
        self.errors = []
        self.__rcopy = {}
        self._using_accounts = None
        self._default_user_name = None
        self._default_password = None

    def __dir__(self):
        return [
            x
            for x in set(list(self.__dict__.keys()) + list(dir(type(self))))
            if x[0] != "_"
        ]

    @property
    def accounts(self):
        if self._using_accounts:
            return [
                (x, self._using_accounts[x][0])
                for x in range(len(self._using_accounts))
            ]
        return None

    async def join(self, room_name: str, anon=False) -> Optional[Room]:
        """
        @parasm room_name: str
        returns a Room object if roomname is valid
        else is going to return None
        """
        room_name = room_name.lower()
        expr = re.compile("^([a-z0-9-]{1,20})$")
        if not expr.match(room_name):
            return None
        if room_name in self._rooms:
            isconnected, canreconnect = AlreadyConnectedError(
                room_name, self._rooms[room_name]
            ).check()
            if not isconnected and canreconnect:
                await self.leave(room_name, True)
                self.check_rooms(room_name)
                await asyncio.sleep(0.2)
        room = Room(self, room_name)
        _accs = [
            self._default_user_name if not anon else "",
            self._default_password if not anon else "",
        ]
        await asyncio.wait_for(room.connect(*_accs), 6.0)

    async def leave(self, room_name: str, reconnect: bool):
        room_name = room_name.lower()
        if room_name not in self._rooms:
            return f"{False if NotConnectedError(room_name) else True}"
        # has to be in the dict until it's fully disconnected
        if room_name in self._rooms and self._rooms[room_name]._connection is not None:
            await self._rooms[room_name].cancel()
            if room_name in self._rooms:
                del self._rooms[room_name]
            await self._call_event("disconnect", room_name)
            if reconnect:
                if self._rooms[room_name].reconnect:
                    self.set_timeout(1, self.join, room_name)
            return True

    async def start(self, user=None, passwd=None, pm=None):
        self._running = True
        await self._call_event("init")
        if pm or self._default_pm == True:
            await self.pm_start(user, passwd)
        await self._call_event("start")
        self._reconnection = asyncio.create_task(self._while_rooms())

    async def _while_rooms(self):
        while True:
            for room in self._rooms:
                isconnected, canreconnect = AlreadyConnectedError(
                    room, self._rooms[room]
                ).check()
                if canreconnect and not isconnected:
                    await self.leave(room, True)
            if not self._running:
                break

    async def pm_start(self, user=None, passwd=None):
        self.pm = PM(self)
        await self.pm.connect(
            user or self._default_user_name, passwd or self._default_password
        )

    @property
    def rooms(self):
        return [self._rooms[x] for x in self._rooms]

    def get_room(self, room_name: str):
        if room_name in [room.name for room in self.rooms]:
            for room in self.rooms:
                if room.name == room_name:
                    return room
        return False

    def check_rooms(self, room):
        for key in self._rooms:
            if key != room:
                self.__rcopy[key] = self._rooms[key]
        self._rooms.clear()
        self._rooms.update(self.__rcopy)
        self.__rcopy.clear()
        return True

    def default_user(
        self,
        user_name: str,
        password: Optional[str] = None,
        pm=True,
        accounts=None,
    ):
        self._using_accounts = accounts  # [[user, pass]]
        self._default_user_name = user_name
        self._default_password = password
        self._default_pm = pm

    async def stop(self):
        self._reconnection.cancel()
        if self.pm and self.pm._connected == True:
            await self.pm.cancel()
            print(f"Disconnected from {self.pm}")
        for room in self.rooms:
            await self.leave(room.name, False)
        self._running = False

    async def enable_bg(self, active=True):
        """Enable background if available."""
        self.bgmode = active
        for room in self._rooms:
            await self._rooms[room].set_bg_mode(int(active))

    @property
    def running(self):
        return self._running

    async def on_event(self, event: str, *args, **kwargs):
        if self.debug:
            print(event, repr(args), repr(kwargs))

    async def _call_event(self, event: str, *args, **kwargs):
        attr = f"on_{event}"
        await self.on_event(event, *args, **kwargs)
        if hasattr(self, attr):
            await getattr(self, attr)(*args, **kwargs)

    def event(self, func, name=None):
        assert inspect.iscoroutinefunction(func)
        if name is None:
            event_name = func.__name__
        else:
            event_name = name
        setattr(self, event_name, func)

    def set_interval(self, tiempo, funcion, *args, **kwargs):
        """
        Llama a una función cada intervalo con los argumentos indicados
        @param funcion: La función que será invocada
        @type tiempo int
        @param tiempo:intervalo
        """
        task = Task(tiempo, funcion, True, *args, **kwargs)

        return task

    def set_timeout(self, tiempo, funcion, *args, **kwargs):
        """
        Llama a una función cada intervalo con los argumentos indicados
        @param tiempo: Tiempo en segundos hasta que se ejecute la función
        @param funcion: La función que será invocada
        """
        task = Task(tiempo, funcion, False, *args, **kwargs)

        return task
