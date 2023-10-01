import logging
from typing import Dict, List, Optional

from .handler import TaskHandler
from .pm import PM
from .room import Room
from .utils import public_attributes


logger = logging.getLogger(__name__)


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

    def get_room(self, room_name: str):
        Room.assert_valid_name(room_name)
        return self.rooms.get(room_name)

    def in_room(self, room_name: str):
        Room.assert_valid_name(room_name)
        return room_name in self.rooms

    def join_room(self, room_name: str):
        Room.assert_valid_name(room_name)
        if self.in_room(room_name):
            logger.error(f"Already joined room {room_name}")
            # Attempt to reconnect existing room?
            return

        self.add_task(self._watch_room(room_name))

    async def _watch_room(self, room_name: str):
        if self._room_class is Room or issubclass(self._room_class, Room):
            room = self._room_class(room_name)
            room.add_listener(self)
            self.rooms[room_name] = room
            await room.listen(self.username, self.password, reconnect=True)
            # Client level reconnect?
            self.rooms.pop(room_name, None)
        else:
            raise TypeError("Client: custom room class does not inherit from Room")

    def leave_room(self, room_name: str):
        room = self.get_room(room_name)
        if room:
            self.add_task(room.disconnect())

    def stop(self):
        if self.pm:
            self.leave_pm()

        for room_name in self.rooms:
            self.leave_room(room_name)

    async def enable_bg(self, active=True):
        """Enable background if available."""
        self.bgmode = active
        for _, room in self.rooms.items():
            await room.set_bg_mode(int(active))
