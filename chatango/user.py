import enum
import time
from typing import Any, Optional
from collections import deque

from .utils import public_attributes
from .resources import (
    fetch_resources,
    PathProvider,
    Styles,
    UserProfile,
    MessageBackground,
)


class ModeratorFlags(enum.IntFlag):
    DELETED = 1 << 0
    EDIT_MODS = 1 << 1
    EDIT_MOD_VISIBILITY = 1 << 2
    EDIT_BW = 1 << 3
    EDIT_RESTRICTIONS = 1 << 4
    EDIT_GROUP = 1 << 5
    SEE_COUNTER = 1 << 6
    SEE_MOD_CHANNEL = 1 << 7
    SEE_MOD_ACTIONS = 1 << 8
    EDIT_NLP = 1 << 9
    EDIT_GP_ANNC = 1 << 10
    EDIT_ADMINS = 1 << 11
    EDIT_SUPERMODS = 1 << 12
    NO_SENDING_LIMITATIONS = 1 << 13
    SEE_IPS = 1 << 14
    CLOSE_GROUP = 1 << 15
    CAN_BROADCAST = 1 << 16
    MOD_ICON_VISIBLE = 1 << 17
    IS_STAFF = 1 << 18
    STAFF_ICON_VISIBLE = 1 << 19


AdminFlags = (
    ModeratorFlags.EDIT_MODS
    | ModeratorFlags.EDIT_RESTRICTIONS
    | ModeratorFlags.EDIT_GROUP
    | ModeratorFlags.EDIT_GP_ANNC
)


class User:
    _users = {}

    def __new__(cls, name, **kwargs):
        key = name.lower()
        if key in User._users:
            user = User._users[key]
        else:
            user = super().__new__(cls)
            setattr(user, "__new_obj", True)
            User._users[key] = user
        return user

    def __init__(self, name, **kwargs):
        if hasattr(self, "__new_obj"):
            self._styles = Styles()
            self._profile = UserProfile()
            self._background = MessageBackground()
            self._name = name.lower()
            self._ip = None
            self._flags = 0
            self._history = deque(maxlen=5)
            self._isanon = kwargs.get("isanon", False)
            self._sids = dict()
            self._showname = name
            self._ispremium = None
            self._puid = str()
            self._client = None
            delattr(self, "__new_obj")

        for attr, val in kwargs.items():
            if attr == "ip" and not val:
                continue  # only valid ips
            setattr(self, "_" + attr, val)

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return "<User name:{} puid:{} ip:{}>".format(
            self.showname, self._puid, self._ip
        )

    @property
    def styles(self) -> Styles:
        return self._styles

    @property
    def profile(self) -> UserProfile:
        return self._profile

    @property
    def background(self) -> MessageBackground:
        return self._background

    @property
    def about(self):
        return self.profile.body_html

    async def load_resources(self):
        """Fetches all user resource files and updates instances."""
        results = await fetch_resources(self.name, [Styles, UserProfile, MessageBackground])
        if len(results) == 3:
            # Explicit casting/assignment for type safety
            self._styles = results[0]
            self._profile = results[1]
            self._background = results[2]

    async def save_styles(self, password: str):
        """Saves current styles to the server via class method."""
        handle = self.name
        if not handle:
            return False
        return await Styles.save(handle, password, self.styles)

    async def save_profile(self, password: str):
        """Saves current profile properties to the server via class method."""
        handle = self.name
        if not handle:
            return False
        return await UserProfile.save(handle, password, self.profile)

    async def save_background(self, password: str):
        """Saves current background properties to the server and notifies rooms."""
        handle = self.name
        if not handle:
            return False

        # 1. Update background configuration via class method
        bg_success = await MessageBackground.save(handle, password, self.background)

        # 2. Update styles (includes the usebackground master toggle)
        style_success = await self.save_styles(password)

        if bg_success or style_success:
            # 3. Notify rooms via websocket
            bg_toggle = "1" if self.styles.use_background else "0"
            for room in self._sids:
                await room.send_command("msgbg", bg_toggle)
                await room.send_command("miu")

        return bg_success and style_success

    def clear_styles(self):
        """Resets the style data to defaults."""
        self._styles = Styles()

    def clear_profile(self):
        """Resets the profile data to defaults."""
        self._profile = UserProfile()

    def clear_background(self):
        """Resets the background data to defaults."""
        self._background = MessageBackground()

    @property
    def fullpic(self):
        if not self.isanon:
            return PathProvider.get_resource_url(self.name, "full.jpg")
        return ""

    @property
    def msgbg(self):
        if not self.isanon:
            return PathProvider.get_resource_url(self.name, "msgbg.jpg")
        return ""

    @property
    def thumb(self):
        if not self.isanon:
            return PathProvider.get_resource_url(self.name, "thumb.jpg")
        return ""

    @property
    def name(self):
        return self._name

    @property
    def puid(self):
        return self._puid

    @property
    def ispremium(self) -> bool:
        return bool(self._ispremium)

    def isowner(self, room) -> bool:
        """Checks if this user is the owner of the given room."""
        return room.owner == self

    @property
    def showname(self):
        return self._showname

    @property
    def isanon(self):
        return self._isanon

    def setName(self, val):
        self._showname = val
        self._name = val.lower()

    def addSessionId(self, room, sid):
        if room not in self._sids:
            self._sids[room] = set()
        self._sids[room].add(sid)

    def getSessionIds(self, room=None):
        if room:
            return self._sids.get(room, set())
        else:
            return set.union(*self._sids.values())

    def removeSessionId(self, room, sid):
        if room in self._sids:
            if not sid:
                self._sids[room].clear()
            elif sid in self._sids[room]:
                self._sids[room].remove(sid)
            if len(self._sids[room]) == 0:
                del self._sids[room]


class Friend:
    def __init__(self, user: User, client: Optional[Any] = None):
        self.user = user
        self.name = user.name
        self._client = client

        self._status = None
        self._idle = None
        self._last_active = None

    def __repr__(self):
        if self.is_friend():
            return f"<Friend {self.name}>"
        return f"<User: {self.name}>"

    def __str__(self):
        return self.name

    def __dir__(self):
        return public_attributes(self)

    @property
    def showname(self):
        return self.user.showname

    @property
    def client(self):
        return self._client

    @property
    def status(self):
        return self._status

    @property
    def last_active(self):
        return self._last_active

    @property
    def idle(self):
        return self._idle

    def is_friend(self):
        if self.client and not self.user.isanon:
            if self.name in self.client.friends:
                return True
            return False
        return None

    async def send_friend_request(self):
        """
        Send a friend request
        """
        if self.client and self.is_friend() == False:
            return await self.client.addfriend(self.name)

    async def unfriend(self):
        """
        Delete friend
        """
        if self.client and self.is_friend() == True:
            return await self.client.unfriend(self.name)

    @property
    def is_online(self):
        return self.status == "online"

    @property
    def is_offline(self):
        return self.status in ["offline", "app"]

    @property
    def is_on_app(self):
        return self.status == "app"

    async def reply(self, message):
        if self.client:
            await self.client.send_message(self.name, message)

    def _check_status(self, _time=None, _idle=None, idle_time=None):  # TODO
        if _time == None and idle_time == None:
            self._last_active = None
            return
        if _idle != None:
            self._idle = _idle
        if self.status == "online" and int(idle_time) >= 1:
            self._last_active = time.time() - (int(idle_time) * 60)
            self._idle = True
        else:
            self._last_active = float(_time)
