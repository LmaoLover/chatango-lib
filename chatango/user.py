import enum
import time
import weakref
from typing import Any, Optional, List, Type, Union, TYPE_CHECKING
from collections import deque

from .utils import public_attributes
from .resources import (
    fetch_resources,
    PathProvider,
    Styles,
    UserProfile,
    MessageBackground,
)

if TYPE_CHECKING:
    from .pm import PM
    from .room import Room


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


def get_anon_name(ts_short: str, aid: str) -> str:
    """Derives an anonymous handle from a short timestamp ID and shortened cookie."""
    if not aid:
        return "anon"
    # aid is either 8-char ShortenedCookie or 16-digit Auth-Token
    aid_part = str(aid).zfill(8)[4:8]
    ts = str(ts_short)
    if not ts or len(ts) < 4:
        ts = "3452"
    else:
        ts = ts.split(".")[0][-4:].zfill(4)
    __reg5 = ""
    __reg1 = 0
    while __reg1 < len(aid_part) and __reg1 < len(ts):
        try:
            __reg4 = int(aid_part[__reg1])
            __reg3 = int(ts[__reg1])
            __reg2 = str(__reg4 + __reg3)
            __reg5 += __reg2[-1:]
        except (ValueError, IndexError):
            __reg5 += "0"
        __reg1 += 1
    return "anon" + __reg5.zfill(4)


class User:
    """Base class for all Chatango users."""

    def __init__(self, **kwargs):
        self._flags = 0
        self._history = deque(maxlen=5)
        self._sessions = weakref.WeakSet()
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

    def add_session(self, session):
        """Register a session (weakly referenced) for this user."""
        self._sessions.add(session)

    def get_sessions(self, room=None):
        """Return the user's currently-alive sessions.

        Sessions are weakly referenced, so entries vanish once they are no
        longer held by a room's session store or a message. The result may
        include offline/superseded sessions that are still referenced; filter
        on ``session.state`` to obtain only currently-online sessions.
        """
        if room:
            return {s for s in self._sessions if s.room == room}
        else:
            return set(self._sessions)


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
                if session.room and session.state is not SessionState.OFFLINE:
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
        self._ts_short = str(kwargs.get("ts_short", "3452"))

    @property
    def aid(self):
        return self._aid

    @property
    def name(self):
        return get_anon_name(self._ts_short, self.aid)


class TemporaryUser(AnonymousUser):
    """An anonymous user with a temporary name."""

    @property
    def name(self) -> str:
        return self.showname.lower()


class SessionState(enum.Enum):
    """How much is known about a session's connection.

    ONLINE: participant data confirmed the connection; nothing revoked it.
    ONLINE_MSG: a recent message; likely present but not confirmed.
    UNKNOWN: old message evidence only; the connection may or may not be present.
    OFFLINE: known gone (leave, absent from snapshot, or superseded).
    """

    ONLINE = enum.auto()
    ONLINE_MSG = enum.auto()
    UNKNOWN = enum.auto()
    OFFLINE = enum.auto()

    @property
    def is_online(self) -> bool:
        """ONLINE or ONLINE_MSG — the states calling code treats as online."""
        return self in (SessionState.ONLINE, SessionState.ONLINE_MSG)


class Session:
    """A room session; last_seen is the latest message timestamp in room time."""

    def __init__(
        self,
        user: User,
        room: Union["Room", "PM"],
        ssid: Optional[str] = None,
        short_cookie: Optional[str] = None,
        encoded_cookie: Optional[str] = None,
        ts_short: Optional[str] = None,
        ip: Optional[str] = None,
        conn_time: Optional[str] = None,
        state: SessionState = SessionState.UNKNOWN,
    ):
        self.user = user
        self.room = room
        self.ssid = ssid
        self.short_cookie = short_cookie
        self.encoded_cookie = encoded_cookie
        self.ts_short = ts_short
        self.ip = ip
        self.conn_time = conn_time
        self.state = state
        self.last_seen = 0.0
        self.disconnect_time: Optional[float] = None

    def __repr__(self):
        name = self.user.showname if self.user else "Unknown"
        return "<Session {} {}{}{}ip:{} state:{} last_seen:{:.1f}>".format(
            name,
            f"{self.short_cookie} " if self.short_cookie else "",
            f"ssid:{self.ssid} " if self.ssid else "",
            f"ts_short:{self.ts_short} " if self.ts_short else "",
            self.ip,
            self.state.name,
            self.last_seen,
        )


class AuthenticatedSession(Session):
    """Our own authenticated connection, enriched by the auth handshake."""

    def __init__(
        self,
        user: User,
        room: Union["Room", "PM"],
        auth_token: Optional[str] = None,
        ts_id: Optional[str] = None,
        correction_time: int = 0,
        badge: int = 0,
        **kwargs,
    ):
        super().__init__(user, room, **kwargs)
        self.auth_token = auth_token
        self.ts_id = ts_id
        self.correction_time = correction_time
        self.badge = badge


class SessionStore(dict):
    """Stores sessions keyed by shortened cookie.

    A cookie can accumulate multiple sessions over time (identity changes,
    reconnects, IP changes). The stored value per key is the full chronological
    history; the default lookup surfaces only the most recent (active) session.
    """

    def __init__(
        self,
        sweep_trigger: int = 3000,
        disconnect_grace: float = 86400.0,
        max_silence: float = 2592000.0,
        message_window: float = 14400.0,
    ):
        super().__init__()
        self.sweep_trigger = sweep_trigger
        self.disconnect_grace = disconnect_grace
        self.max_silence = max_silence
        self.message_window = message_window

    def __getitem__(self, cookie: str) -> Session:
        session = self.get(cookie)
        if session is None:
            raise KeyError(cookie)
        return session

    def get(self, cookie: str, default=None) -> Optional[Session]:
        history = dict.get(self, cookie)
        if not history:
            return default
        return max(
            history,
            key=lambda session: (session.state.is_online, session.last_seen),
        )

    def __setitem__(self, cookie: str, session: Session) -> None:
        if session.short_cookie != cookie:
            raise ValueError(
                f"Session cookie {session.short_cookie!r} does not match key {cookie!r}"
            )
        self.add(session)

    def __iter__(self):
        for cookie in dict.keys(self):
            session = self.get(cookie)
            if session is not None:
                yield session

    def __len__(self) -> int:
        return dict.__len__(self)

    def add(
        self,
        session: Session,
        *,
        state: Optional[SessionState] = None,
        seen_at: Optional[float] = None,
        supersede: bool = False,
    ) -> None:
        """Store a session; supersede marks the cookie's other sessions OFFLINE."""
        cookie = session.short_cookie
        history = dict.get(self, cookie)
        if history is None:
            history = []
            dict.__setitem__(self, cookie, history)

        if session not in history:
            history.append(session)
        if seen_at is not None:
            session.last_seen = max(session.last_seen, seen_at)
        if state is not None:
            session.state = state

        if supersede:
            for candidate in history:
                if candidate is not session:
                    candidate.state = SessionState.OFFLINE
            session.disconnect_time = None
        history.sort(key=lambda item: item.last_seen)

    def activate(self, session: Session, seen_at: Optional[float] = None) -> None:
        """Make this session the cookie's ONLINE one (its siblings go OFFLINE)."""
        history = dict.get(self, session.short_cookie)
        if history is None or session not in history:
            raise ValueError("Cannot activate a session that is not in the store")

        if seen_at is not None:
            session.last_seen = max(session.last_seen, seen_at)
        for candidate in history:
            candidate.state = (
                SessionState.ONLINE if candidate is session else SessionState.OFFLINE
            )
        session.disconnect_time = None
        history.sort(key=lambda item: item.last_seen)

    def find(
        self,
        cookie: str,
        user: User,
        ip: Optional[str] = None,
        encoded_cookie: Optional[str] = None,
    ) -> Optional[Session]:
        """Find the newest session matching identity and available message fields."""
        history = dict.get(self, cookie, [])
        matches = []
        for session in history:
            if session.user is not user:
                continue
            if session.ip and ip and session.ip != ip:
                continue
            if (
                session.encoded_cookie
                and encoded_cookie
                and session.encoded_cookie != encoded_cookie
            ):
                continue
            matches.append(session)

        def match_rank(session):
            ip_match = bool(ip and session.ip == ip)
            cookie_match = bool(encoded_cookie and session.encoded_cookie == encoded_cookie)
            return ip_match, cookie_match, session.last_seen

        return max(matches, key=match_rank, default=None)

    def touch(self, session: Session, seen_at: float) -> None:
        """Record an event timestamp without changing the session's state."""
        history = dict.get(self, session.short_cookie)
        if history is None or session not in history:
            raise ValueError("Cannot update a session that is not in the store")
        session.last_seen = max(session.last_seen, seen_at)
        history.sort(key=lambda item: item.last_seen)

    def promote(self, session: Session, seen_at: float) -> None:
        """Live message: make this the cookie's ONLINE_MSG session; siblings go OFFLINE."""
        history = dict.get(self, session.short_cookie)
        if history is None or session not in history:
            raise ValueError("Cannot update a session that is not in the store")
        if session.state is not SessionState.ONLINE:
            session.state = SessionState.ONLINE_MSG
        for candidate in history:
            if candidate is not session:
                candidate.state = SessionState.OFFLINE
        session.disconnect_time = None
        session.last_seen = max(session.last_seen, seen_at)
        history.sort(key=lambda item: item.last_seen)

    def deactivate(
        self,
        cookie: str,
        disconnected_at: Optional[float] = None,
    ) -> Optional[Session]:
        """Mark the cookie's current session offline; retains its record."""
        session = self.get(cookie)
        if session is None:
            return None
        session.state = SessionState.OFFLINE
        session.disconnect_time = (
            time.time() if disconnected_at is None else disconnected_at
        )
        dict.__getitem__(self, cookie).sort(key=lambda item: item.last_seen)
        return session

    def sessions_for(self, cookie: str) -> List[Session]:
        """All sessions for a cookie, oldest first."""
        return list(dict.get(self, cookie, []))

    def active_sessions(self) -> List[Session]:
        """Return every retained session currently treated as online."""
        return [s for s in self if s.state.is_online]

    def all_sessions(self) -> List[Session]:
        """Every retained session, active and inactive."""
        result = []
        for history in dict.values(self):
            result.extend(history)
        return result

    def sweep(self, now: float) -> None:
        """Age message-active sessions, then evict stale records above the trigger."""
        # A message-active session that has gone quiet is demoted, not dropped.
        for history in dict.values(self):
            for session in history:
                if (
                    session.state is SessionState.ONLINE_MSG
                    and now - session.last_seen > self.message_window
                ):
                    session.state = SessionState.UNKNOWN
        total = sum(len(history) for history in dict.values(self))
        if total <= self.sweep_trigger:
            return
        for cookie in list(dict.keys(self)):
            history = dict.__getitem__(self, cookie)
            kept = [s for s in history if not self._evictable(s, now)]
            if kept:
                kept.sort(key=lambda item: item.last_seen)
                dict.__setitem__(self, cookie, kept)
            else:
                dict.__delitem__(self, cookie)

    def _evictable(self, session: Session, now: float) -> bool:
        if session.state.is_online:
            # ONLINE and ONLINE_MSG sessions are never evicted for silence.
            return False
        if session.state is SessionState.UNKNOWN:
            # Message evidence only: keep until it has been silent a long time.
            return now - session.last_seen > self.max_silence
        return now - (session.disconnect_time or session.last_seen) > self.disconnect_grace


class UserManager:
    """Manages the lifecycle and caching of User objects."""

    _users = weakref.WeakValueDictionary()

    def __init__(self):
        raise RuntimeError(
            "UserManager cannot be instantiated. Use UserManager.get_user() instead."
        )

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
                if "ts_short" in kwargs:
                    user._ts_short = str(kwargs["ts_short"])

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
