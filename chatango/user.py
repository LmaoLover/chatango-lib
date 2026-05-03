import enum
import time
import weakref
from typing import Any, Optional, Dict, Type
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


def get_anon_name(tssid: str, aid: str) -> str:
    """Derives an anonymous handle from a tag ID and shortened cookie."""
    aid = aid.zfill(8)[4:8]
    ts = str(tssid)
    if not ts or len(ts) < 4:
        ts = "3452"
    else:
        ts = ts.split(".")[0][-4:]
    __reg5 = ""
    __reg1 = 0
    while __reg1 < len(aid):
        __reg4 = int(aid[__reg1])
        __reg3 = int(ts[__reg1])
        __reg2 = str(__reg4 + __reg3)
        __reg5 += __reg2[-1:]
        __reg1 += 1
    return "anon" + __reg5.zfill(4)


class User:
    """Base class for all Chatango users."""

    def __init__(self, **kwargs):
        self._flags = 0
        self._history = deque(maxlen=5)
        self._sessions = weakref.WeakSet()  # set(Session)
        self._ispremium = None
        self._client = None
        self._showname = kwargs.get("showname")

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return "<{} name:{} sid:{} aid:{}>".format(
            self.__class__.__name__, self.showname, self.sid, self.aid
        )

    @property
    def styles(self) -> Styles:
        """Read-only default styles for base users. Overridden in RegisteredUser."""
        return Styles()

    @property
    def profile(self) -> UserProfile:
        """Read-only default profile for base users. Overridden in RegisteredUser."""
        return UserProfile()

    @property
    def background(self) -> MessageBackground:
        """Read-only default background for base users. Overridden in RegisteredUser."""
        return MessageBackground()

    @property
    def about(self):
        return self.profile.body_html

    async def load_resources(self):
        """Base users have no resources to fetch."""
        pass

    async def save_styles(self, password: str) -> bool:
        """Base users cannot save styles."""
        return False

    async def save_profile(self, password: str) -> bool:
        """Base users cannot save profile."""
        return False

    async def save_background(self, password: str) -> bool:
        """Base users cannot save background."""
        return False

    def clear_styles(self):
        """Base users have no style data to clear."""
        pass

    def clear_profile(self):
        """Base users have no profile data to clear."""
        pass

    def clear_background(self):
        """Base users have no background data to clear."""
        pass

    @property
    def fullpic(self) -> str:
        return ""

    @property
    def msgbg(self) -> str:
        return ""

    @property
    def thumb(self) -> str:
        return ""

    @property
    def name(self) -> str:
        """Lowercase identifier for the user."""
        return self._showname.lower() if self._showname else ""

    @property
    def showname(self) -> str:
        return self._showname or self.name

    @property
    def sid(self):
        return None

    @property
    def aid(self):
        return None

    @property
    def ispremium(self) -> bool:
        return bool(self._ispremium)

    @ispremium.setter
    def ispremium(self, value):
        self._ispremium = bool(value) if value is not None else None

    def isowner(self, room) -> bool:
        """Checks if this user is the owner of the given room."""
        return room.owner == self

    @property
    def isanon(self):
        return not isinstance(self, RegisteredUser)

    @property
    def istemp(self):
        return isinstance(self, TemporaryUser)

    def addSession(self, session):
        self._sessions.add(session)

    def getSessions(self, room=None):
        if room:
            return {s for s in self._sessions if s.room == room}
        else:
            return set(self._sessions)

    def removeSession(self, session):
        if session in self._sessions:
            self._sessions.remove(session)


class RegisteredUser(User):
    """A registered Chatango user."""

    def __init__(self, name, **kwargs):
        super().__init__(**kwargs)
        self._name = name.lower()
        self._showname = name
        self._styles = Styles()
        self._profile = UserProfile()
        self._background = MessageBackground()

    @property
    def name(self):
        return self._name

    @property
    def sid(self):
        return self._name

    @property
    def styles(self) -> Styles:
        return self._styles

    @property
    def profile(self) -> UserProfile:
        return self._profile

    @property
    def background(self) -> MessageBackground:
        return self._background

    async def load_resources(self):
        """Fetches all user resource files and updates instances."""
        results = await fetch_resources(
            self.name, [Styles, UserProfile, MessageBackground]
        )
        if len(results) == 3:
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

        bg_success = await MessageBackground.save(handle, password, self.background)
        style_success = await self.save_styles(password)

        if bg_success or style_success:
            bg_toggle = "1" if self.styles.use_background else "0"
            for session in self._sessions:
                if session.room:
                    await session.room.send_command("msgbg", bg_toggle)
                    await session.room.send_command("miu")

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
        return PathProvider.get_resource_url(self.name, "full.jpg")

    @property
    def msgbg(self):
        return PathProvider.get_resource_url(self.name, "msgbg.jpg")

    @property
    def thumb(self):
        return PathProvider.get_resource_url(self.name, "thumb.jpg")


class AnonymousUser(User):
    """A generic anonymous Chatango user."""

    def __init__(self, aid, **kwargs):
        super().__init__(**kwargs)
        self._aid = aid
        self._display_id = str(kwargs.get("display_id", "3452"))

    @property
    def aid(self):
        return self._aid

    @property
    def name(self):
        return get_anon_name(self._display_id, self.aid)


class TemporaryUser(AnonymousUser):
    """An anonymous user with a temporary name."""

    @property
    def name(self) -> str:
        return self.showname.lower()


class Session:
    """Represents an active connection session to a room."""

    def __init__(
        self,
        user: Optional[User] = None,
        room: Optional[Any] = None,
        session_id: Optional[str] = None,
        short_cookie: Optional[str] = None,
        encoded_cookie: Optional[str] = None,
        ts_id: Optional[str] = None,
        ip: Optional[str] = None,
        conn_time: Optional[str] = None,
        correction_time: int = 0,
        badge: int = 0
    ):
        self.user = user
        self.room = room
        self.session_id = session_id  # SSID
        self.short_cookie = short_cookie # AID
        self.encoded_cookie = encoded_cookie
        self.ts_id = ts_id
        self.ip = ip
        self.conn_time = conn_time
        self.correction_time = correction_time
        self.badge = badge

    def __repr__(self):
        name = self.user.showname if self.user else "Unknown"
        return f"<Session user:{name} ssid:{self.session_id} ip:{self.ip}>"


class UserManager:
    """Manages the lifecycle and caching of User objects."""

    _users = weakref.WeakValueDictionary()

    def __init__(self):
        raise RuntimeError("UserManager cannot be instantiated. Use UserManager.get_user() instead.")

    @classmethod
    def get_user(
        cls,
        name: Optional[str] = None,
        aid: Optional[str] = None,
        **kwargs,
    ) -> User:
        """Retrieves or creates a User instance."""
        # Normalize "None" and empty strings
        if name == "None" or not name:
            name = None
        if aid == "None" or not aid:
            aid = None

        if name and not aid:
            # Registered User
            sid = name.lower()
            key = f"R:{sid}"
            user_cls = RegisteredUser
            id_val = name
        elif name and aid:
            # Temporary User
            key = f"T:{aid}"
            user_cls = TemporaryUser
            id_val = aid
            kwargs["showname"] = name
        elif aid:
            # Anonymous User
            key = f"A:{aid}"
            user_cls = AnonymousUser
            id_val = aid
        else:
            # Not enough info to cache/identify uniquely
            return AnonymousUser(aid or "", showname=name, **kwargs)

        if key in cls._users:
            user = cls._users[key]
            # Update transient attributes if provided
            if isinstance(user, AnonymousUser):
                if "display_id" in kwargs:
                    user._display_id = str(kwargs["display_id"])
            
            # Unified name update
            if name:
                user._showname = name
            return user

        user = user_cls(id_val, **kwargs)
        cls._users[key] = user
        return user


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
