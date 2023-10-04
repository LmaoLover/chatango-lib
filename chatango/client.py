import asyncio
import logging
from typing import Dict, List, Optional

from .handler import TaskHandler
from .pm import PM
from .room import Room
from .utils import public_attributes


logger = logging.getLogger(__name__)


class ConnectionListener:
    def __init__(self, client):
        self.client = client

    async def on_connect(self, room):
        self.client.initial_rooms_connected.append(room.name)


class Client(TaskHandler):
    def __init__(
        self,
        username: str = "",
        password: str = "",
        rooms: List[str] = [],
        pm=False,
        room_class=Room,
        pm_class=PM,
    ):
        self._room_class = room_class
        self._pm_class = pm_class
        self.running = False
        self.rooms: Dict[str, Room] = {}
        self.pm: Optional[PM] = None
        self.use_pm = pm
        self.initial_rooms: List[str] = rooms
        self.initial_rooms_connected: List[str] = []
        self.username = username
        self.password = password

    def __dir__(self):
        return public_attributes(self)

    async def run(self, *, forever=False):
        self.running = True

        if not forever and not self.use_pm and not self.initial_rooms:
            logger.error("No rooms or PM to join. Exiting.")
            return

        if self.use_pm:
            self.join_pm()

        for room_name in self.initial_rooms:
            self.join_room(room_name)

        self.add_task(self.confirm_connected())

        if forever:
            await self.task_loop
        else:
            await self.complete_tasks()
        self.running = False

    def join_pm(self):
        if not self.username or not self.password:
            logger.error("PM requires username and password.")
            return

        self.add_task(self._watch_pm())

    async def _watch_pm(self):
        if self._pm_class is PM or issubclass(self._pm_class, PM):
            pm = self._pm_class()
            pm.add_listener(self)
            self.pm = pm
            await pm.listen(self.username, self.password, reconnect=True)
            self.pm = None
        else:
            raise TypeError("Client: custom PM class does not inherit from PM")

    def leave_pm(self):
        if self.pm:
            self.add_task(self.pm.disconnect())

    def join_room(self, room_name: str):
        Room.assert_valid_name(room_name)
        if room_name in self.rooms:
            logger.error(f"Already joined room {room_name}")
            # Attempt to reconnect existing room?
            return

        self.add_task(self._watch_room(room_name))

    async def _watch_room(self, room_name: str):
        if self._room_class is Room or issubclass(self._room_class, Room):
            room = self._room_class(room_name)
            room.add_listener(self)
            room.add_listener(ConnectionListener(self))
            self.rooms[room_name] = room
            await room.listen(self.username, self.password, reconnect=True)
            # Client level reconnect?
            self.rooms.pop(room_name, None)
        else:
            raise TypeError("Client: custom room class does not inherit from Room")

    def leave_room(self, room_name: str):
        room = self.rooms.get(room_name)
        if room:
            self.add_task(room.disconnect())

    def stop(self):
        if self.pm:
            self.leave_pm()

        for room_name in self.rooms:
            self.leave_room(room_name)

    connection_check_timeout = 5

    async def confirm_connected(self):
        try:
            await asyncio.wait_for(
                self.connection_checker(), timeout=self.connection_check_timeout
            )
        except asyncio.TimeoutError:
            problem_rooms = set(self.initial_rooms) - set(self.initial_rooms_connected)
            logger.error(f"Failed to connect: {', '.join(problem_rooms)}")
            self.add_task(self.on_started())

    async def connection_checker(self):
        while True:
            if set(self.initial_rooms) == set(self.initial_rooms_connected):
                self.add_task(self.on_started())
                break
            await asyncio.sleep(0.1)

    """
    Callback for child classes, called when all initial rooms are connected,
    or after a timeout specified by class attribute connection_check_timeout.
    """

    async def on_started(self):
        pass
