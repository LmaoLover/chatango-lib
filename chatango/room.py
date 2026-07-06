import asyncio
import html
import time
import enum
import re
import logging
import urllib.parse as urlreq

from collections import deque, namedtuple
from typing import Optional, Tuple
from attr import dataclass

from .utils import (
    get_server,
    _id_gen,
    public_attributes,
)
from .message import (
    Message,
    MessageFlags,
    RoomMessage,
    _process,
    message_cut,
    Command,
    MessageHistory,
)
from .user import RegisteredUser, User, ModeratorFlags, AdminFlags, UserManager, Session
from .resources import RoomProfile, fetch_resources
from .exceptions import AlreadyConnectedError, InvalidRoomNameError
from .handler import EventHandler
from .connection import WebsocketConnection

logger = logging.getLogger(__name__)


class RoomFlags(enum.IntFlag):
    LIST_TAXONOMY = 1 << 0
    NO_ANONS = 1 << 2
    NO_FLAGGING = 1 << 3
    NO_COUNTER = 1 << 4
    NO_IMAGES = 1 << 5
    NO_LINKS = 1 << 6
    NO_VIDEOS = 1 << 7
    NO_STYLED_TEXT = 1 << 8
    NO_LINKS_CHATANGO = 1 << 9
    NO_BROADCAST_MSG_WITH_BW = 1 << 10
    RATE_LIMIT_REGIMEON = 1 << 11
    CHANNELS_DISABLED = 1 << 13
    NLP_SINGLEMSG = 1 << 14
    NLP_MSGQUEUE = 1 << 15
    BROADCAST_MODE = 1 << 16
    CLOSED_IF_NO_MODS = 1 << 17
    IS_CLOSED = 1 << 18
    SHOW_MOD_ICONS = 1 << 19
    MODS_CHOOSE_VISIBILITY = 1 << 20
    NLP_NGRAM = 1 << 21
    NO_PROXIES = 1 << 22
    HAS_XML = 1 << 28
    UNSAFE = 1 << 29


class Room(WebsocketConnection, EventHandler):
    _BANDATA = namedtuple("BanData", ["encoded_cookie", "ip", "target", "time", "src"])

    valid_name = re.compile("^([a-z0-9-]{1,20})$")

    command_responses = {
        "v": "v",
        "bauth": "ok",
        "blogin": ("pwdok", "badlogin", "aliasok", "badalias"),
        "getpremium": "premium",
        "getannouncement": "getannc",
        "getbannedwords": "bw",
        "getratelimit": "getratelimit",
        "gparticipants": "gparticipants",
        "updategroupflags": "groupflagstoggled",
        "get_more": ("gotmore", "nomore"),
    }

    @classmethod
    def assert_valid_name(cls, room_name: str):
        if not Room.valid_name.match(room_name):
            raise InvalidRoomNameError(room_name)

    @dataclass(repr=False)
    class Announcement:
        flags: int = 0
        room_name: str = ""
        message_raw: str = ""
        period: Optional[int] = None
        message_delay: Optional[int] = None

        def __repr__(self):
            return '<Announcement {} {} "{}"{}{}>'.format(
                self.room_name,
                "enabled" if self.enabled else "disabled",
                self.message,
                f" period:{self.period}" if self.period else "",
                f" message_delay:{self.message_delay}" if self.message_delay else "",
            )

        @property
        def enabled(self) -> bool:
            return bool(self.flags & 1)

        @property
        def message(self) -> str:
            return Message.clean_body_text(self.message_raw)

    def __init__(self, name: str):
        WebsocketConnection.__init__(self)
        EventHandler.__init__(self)
        self.reconnect = False
        self.silent = False
        self.message_flags = 0
        self._reset_state(name)

    def _reset_state(self, name: str):
        self.assert_valid_name(name)
        self._name = name
        self._server = get_server(name)
        self._owner: Optional[User] = None
        self._session: Optional[Session] = None
        self._flags: Optional[RoomFlags] = None
        self._version: Optional[int] = None
        self._profile: Optional[RoomProfile] = None
        self._announcement: Optional[Room.Announcement] = None
        self._banned_words: Optional[Tuple[str, str]] = None
        self._rate_limit: Optional[int] = None
        self._mqueue = dict()
        self._uqueue = dict()
        self._history = MessageHistory(maxlen=3000)
        self._userdict = dict()
        self._usercount: Optional[int] = None
        self._anoncount: Optional[int] = None
        self._mods = dict()
        self._banlist = dict()
        self._unbanlist = dict()
        self._unbanqueue = deque(maxlen=500)
        self._maxlen = 2800
        self._bgmode = 0
        self._gotmore: Optional[int] = None
        self._nomore = False

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return f"<Room {self.name}>"

    @property
    def name(self) -> str:
        return self._name

    @property
    def server(self) -> str:
        return self._server

    @property
    def owner(self) -> User:
        if self._owner is not None:
            return self._owner
        else:
            raise AttributeError("Owner not available, room not connected")

    @property
    def session(self) -> Session:
        if self._session is not None:
            return self._session
        else:
            raise AttributeError("Session not available, room not connected")

    @property
    def user(self) -> User:
        return self.session.user

    @property
    def flags(self) -> RoomFlags:
        if self._flags is not None:
            return self._flags
        else:
            raise AttributeError("Flags not available, room not connected")

    @property
    def version(self) -> int:
        if self._version is not None:
            return self._version
        else:
            raise AttributeError("Version not available, room not connected")

    @property
    def profile(self) -> RoomProfile:
        if self._profile is not None:
            return self._profile
        else:
            raise AttributeError("Profile not available, first call load_profile")

    @property
    def title(self) -> str:
        return self.profile.group_title

    @property
    def description(self) -> str:
        return self.profile.group_body_html

    @property
    def announcement(self) -> Announcement:
        if self._announcement is not None:
            return self._announcement
        else:
            raise AttributeError(
                "Announcement not available, first send the getannc command"
            )

    @property
    def banned_words(self) -> Tuple[str, str]:
        if self._banned_words is not None:
            return self._banned_words
        else:
            raise AttributeError(
                "Banned words not available, first send the getbannedwords command"
            )

    @property
    def rate_limit(self) -> int:
        if self._rate_limit is not None:
            return self._rate_limit
        else:
            raise AttributeError(
                "Rate limit not available, first send the getratelimit command"
            )

    @property
    def messages(self) -> MessageHistory:
        return self._history

    @property
    def history(self) -> MessageHistory:
        return self._history

    @property
    def is_pm(self):
        return False

    @property
    def badge(self):
        badge_val = self.session.badge if self._session else None

        if not badge_val:
            return 0
        elif badge_val == 1:
            return MessageFlags.SHOW_MOD_ICON.value
        elif badge_val == 2:
            return MessageFlags.SHOW_STAFF_ICON.value
        else:
            return 0

    @property
    def unbanlist(self):
        """Lista de usuarios desbaneados"""
        return list(set(x.target.name for x in self._unbanqueue))

    @property
    def banlist(self):
        return list(self._banlist.keys())

    @property
    def mods(self):
        return set(self._mods.keys())

    @property
    def userlist(self):
        if self._anoncount is not None:
            return [s.user for s in self._userdict.values()]
        else:
            raise AttributeError(
                "User list not available, first send the gparticipants command"
            )

    @property
    def usercount(self):
        if self._usercount is not None:
            return self._usercount
        elif self._anoncount is not None:
            return self._anoncount + len(
                [u for u in self.userlist if isinstance(u, RegisteredUser)]
            )
        else:
            raise AttributeError(
                "User list not available, first send the gparticipants command"
            )

    @property
    def anoncount(self):
        if self._anoncount is not None:
            return self._anoncount
        else:
            raise AttributeError(
                "User list not available, first send the gparticipants command"
            )

    async def _connect_server(self):
        """
        Connect to the websocket server
        """
        if self.connected:
            raise AlreadyConnectedError(self.name)

        await self._connect(f"wss://{self.server}:8081/")
        self.call_event("connect")

    async def _disconnect(self):
        """
        Disconnect from the websocket server
        """
        await super()._disconnect()
        self._reset_state(self.name)

    async def _connection_wait(self):
        """
        Wait until the websocket disconnects
        """
        if self._recv_task:
            await self._recv_task
            self.call_event("disconnect")

    async def disconnect(self):
        """
        Force this room to disconnect
        """
        self.reconnect = False
        await self._disconnect()

    async def bounce(self):
        """
        Disconnect but allow reconnection
        """
        await self._disconnect()

    async def listen(self, user_name: str = "", password: str = "", reconnect=False):
        """
        Connect, login, and listen to websocket server
        """
        self.reconnect = reconnect
        while True:
            try:
                await self._connect_server()
                await self._initialize(user_name, password)
                await self._connection_wait()
            except ConnectionError:
                pass
            finally:
                await self._disconnect()
            if not self.reconnect:
                break
            await asyncio.sleep(3)
        self.end_tasks()

    async def _initialize(self, user_name: str = "", password: str = ""):
        """
        Send websocket commands to connect and login to the room
        """
        try:
            await self.send_command("v", expect="v")
            await self._auth(user_name, password, expect="ok")
            if self.user.isanon:
                await self.send_command("msgbg", "0")
            else:
                await self.get_premium()
                await self._style_init(self.user)
            await self.get_room_info()
        except TimeoutError as e:
            logger.error(f"Failed initialization handshake for {self.name}: {e}")
            raise ConnectionError() from e

    async def _auth(self, user_name: str = "", password: str = "", **kwargs):
        """
        Send bauth command to login to this room
        """
        auth_token = self.session.auth_token if self._session else ""
        return await self.send_command(
            "bauth",
            self.name,
            auth_token,
            user_name,
            password,
            **kwargs,
        )

    async def login(self, user_name: str = "", password: str = "", **kwargs):
        """
        Login after having connected as anon
        """
        result = await self.send_command(
            "blogin", user_name, password, **kwargs, wait_for_response=True
        )
        if not result:
            return
        elif result.name == "pwdok":
            self.session.user = UserManager.get_user(name=user_name)
            await self.get_premium()
            await self._style_init(self.user)
        elif result.name == "aliasok" and self.session.auth_token:
            self.session.user = UserManager.get_user(
                name=user_name, aid=self.session.auth_token
            )
        return result

    async def logout(self):
        await self.send_command("blogout")

    async def send_command(self, *args, **kwargs):
        command = args[0]
        if command in ["v", "bauth"]:
            kwargs["terminator"] = "\x00"
        if kwargs.pop("wait_for_response", None):
            kwargs["expect"] = Room.command_responses[command]
        return await super().send_command(*args, **kwargs)

    async def send_message(self, message, *, use_html=False, flags=None, **kwargs):
        if not self.silent:
            message_flags = (
                flags if flags else self.message_flags + self.badge or 0 + self.badge
            )
            msg = str(message)
            if not use_html:
                msg = html.escape(msg, quote=False)
            msg = msg.replace("\n", "\r").replace("~", "&#126;")
            for msg in message_cut(msg, self._maxlen):
                is_anon = self.user.isanon
                ts_short = self.session.ts_short if self._session else None
                styled_msg = f"{self.user.styles.get_name_tag(is_anon, ts_short)}{self.user.styles.format_message(msg, is_anon=is_anon)}"
                await self.send_command("bm", _id_gen(), int(message_flags), styled_msg)

    async def get_room_info(self):
        """Requests initial state data from server."""
        await self.get_announcement()
        await self.get_banned_words()
        await self.get_rate_limit()
        # TODO these don't work
        # await self.request_banlist()
        # await self.request_unbanlist()
        await self.load_profile()

    async def get_premium(self, **kwargs):
        """Request logged in user's premium status"""
        return await self.send_command("getpremium", **kwargs)

    async def get_announcement(self, **kwargs):
        """Request the room's scheduled announcement info"""
        return await self.send_command("getannouncement", **kwargs)

    async def get_banned_words(self, **kwargs):
        """Request the room's banned words"""
        return await self.send_command("getbannedwords", **kwargs)

    async def get_rate_limit(self, **kwargs):
        """Request the room's message posting rate limit"""
        return await self.send_command("getratelimit", **kwargs)

    async def load_profile(self):
        """Fetches the group profile resource and updates instance."""
        results = await fetch_resources(self.name, [RoomProfile])
        if results:
            self._profile = results[0]
            self.call_event("profile")

    async def save_profile(self, password: str):
        """Saves current room profile properties to the server via class method."""
        handle = self.user.name
        if not handle:
            return False
        return await RoomProfile.save(self.name, password, self.profile)

    def _delete_history(self, msgid) -> Optional[RoomMessage]:
        """
        Marks a message as deleted in the local history and returns it.
        @param msgid: Unique message ID
        """
        if msg := self._history.get(msgid, None):
            msg.deleted = True
            return msg
        else:
            return None

    def get_last_message(self, user=None) -> Optional[RoomMessage]:
        """
        Finds the most recent message in history, optionally for a specific user.
        @param user: User object or name string
        """
        if not self._history:
            return None
        if not user:
            return self._history.last()
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        return next((m for m in reversed(self._history) if m.user == user), None)

    async def get_more(self, n: int = 20, **kwargs):
        """
        Requests an additional batch of historical messages from the server.
        Format: get_more:count:request_id
        @param n: Number of messages to request (1-50)
        """
        if n < 1 or n > 50:
            raise ValueError("History size must be between 1-50.")
        req_id = self._gotmore + 1 if self._gotmore is not None else 0
        return await self.send_command("get_more", str(n), str(req_id), **kwargs)

    def set_font(
        self, name_color=None, font_color=None, font_size=None, font_face=None
    ):
        if name_color:
            self.user.styles.name_color = str(name_color)
        if font_color:
            self.user.styles.font_color = str(font_color)
        if font_size:
            self.user.styles.font_size = int(font_size)
        if font_face:
            self.user.styles.font_face = int(font_face)

    async def enable_bg(self):
        await self.set_bg_mode(1)

    async def disable_bg(self):
        await self.set_bg_mode(0)

    def get_level(self, user):
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        if user == self.owner:
            return 3
        mod_user = self._mods.get(user)
        if not mod_user:
            return 0
        elif mod_user.isadmin:
            return 2
        else:
            return 1

    def ban_record(self, user):
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        return self._banlist.get(user)

    async def _raw_unban(self, name, ip, encoded_cookie):
        await self.send_command("removeblock", encoded_cookie, ip, name)

    async def unban_user(self, user):
        rec = self.ban_record(user)
        print("rec", rec)
        if rec:
            await self._raw_unban(rec.target.name, rec.ip, rec.encoded_cookie)
            return True
        else:
            return False

    async def ban_message(self, msg: RoomMessage) -> bool:
        if self.get_level(self.user) > 0:
            name = "" if msg.user.isanon else msg.user.name
            await self._raw_ban(msg.encoded_cookie, msg.ip, name)
            return True
        return False

    async def _raw_ban(self, encoded_cookie, ip, name):
        """
        Ban user with received data
        @param encoded_cookie: Encoded cookie
        @param ip: user IP
        @param name: chatango user name
        @return: bool
        """
        await self.send_command("block", encoded_cookie, ip, name)

    async def ban_user(self, user: str) -> bool:
        """
        Banear un usuario (si se tiene el privilegio)
        @param user: El usuario, str o User
        @return: Bool indicando si se envió el comando
        """
        msg = self.get_last_message(user)
        if msg and msg.user not in self.banlist:
            return await self.ban_message(msg)
        return False

    async def clear_all(self, **kwargs):
        """
        Clears all messages from the room history.
        Format: clearall
        """
        # TODO bot user privilege check
        return await self.send_command("clearall", **kwargs)

    async def delete_message(self, message, **kwargs):
        """
        Deletes a single message from the room.
        Format: delmsg:msg_id
        @param message: RoomMessage object
        """
        # TODO bot user privilege check
        if message and message.id:
            return await self.send_command("delmsg", message.id, **kwargs)
        else:
            raise ValueError("Invalid message, cannot delete")

    async def delete_user_last_message(self, user, **kwargs):
        """
        Deletes the most recent message sent by a specific user.
        @param user: User object or name string
        """
        # TODO bot user privilege check
        msg = self.get_last_message(user)
        if msg:
            return await self.delete_message(msg, **kwargs)
        else:
            raise ValueError("No message for user, cannot delete")

    async def delete_user_all(self, user, **kwargs):
        """
        Deletes all messages sent by a specific user in the current session.
        @param user: User object or name string
        """
        # TODO bot user privilege check
        # TODO fetch all msg ids for user
        # return await self.send_command("delallmsg", *msgids, **kwargs)
        raise AttributeError("delete_user_all not implemented")

    async def request_unbanlist(self):
        await self.send_command(
            "blocklist",
            "unblock",
            str(int(time.time() + self.session.correction_time)),
            "next",
            "500",
            "anons",
            "1",
        )

    async def request_banlist(self):  # TODO revisar
        await self.send_command(
            "blocklist",
            "block",
            str(int(time.time() + self.session.correction_time)),
            "next",
            "500",
            "anons",
            "1",
        )

    async def set_banned_words(self, part="", whole=""):
        """
        Actualiza las palabras baneadas en el servidor
        @param part: Las partes de palabras que serán baneadas (separadas por
        coma, 4 carácteres o más)
        @param whole: Las palabras completas que serán baneadas, (separadas
        por coma, cualquier tamaño)
        """
        if self.user in self._mods and ModeratorFlags.EDIT_BW in self._mods[self.user]:
            await self.send_command(
                "\x00setbannedwords", urlreq.quote(part), urlreq.quote(whole)
            )
            return True
        return False

    async def request_participants(self):
        """Enables participant mode."""
        await self.send_command("gparticipants")

    async def stop_participants(self):
        """Disables participant mode."""
        await self.send_command("gparticipants", "stop")

    async def set_bg_mode(self, mode):
        self._bgmode = mode
        if self.connected:
            await self.send_command("getpremium")
            if self.user.ispremium:
                await self.send_command("msgbg", str(self._bgmode))

    async def _style_init(self, user):
        if not user.isanon:
            await user.load_resources()
        else:
            self.set_font(
                name_color="000000", font_color="000000", font_size=11, font_face=1
            )

    #
    # Received Command Handlers
    #

    async def handle_v(self, cmd: Command):
        """
        Reports the minimum and current protocol versions supported by the server

        Format: v:minimum_version:current_version
        """
        self._version = int(cmd.fields[2])
        self.call_event("v")

    async def handle_ok(self, cmd: Command):
        """
        Processes the 'ok' command which signals successful connection and
        provides room/user metadata.

        Format: ok:OWNER:AUTH_TOKEN:LOGIN_STATUS:CURRENT_NAME:SERVER_TS:IP:MODS:FLAGS
        """
        (
            owner_name,
            auth_token,
            login_status,
            current_name,
            ts_id,
            ip,
            mods_str,
            flags_str,
        ) = cmd.fields[1:]

        ts_short = ts_id.split(".")[0][-4:].zfill(4)

        # 1. Resolve current User
        if login_status == "M":
            user = UserManager.get_user(name=current_name)
            await self._style_init(user)
        else:
            # Create an anon user with Auth-Token as aid.
            user = UserManager.get_user(aid=auth_token, ts_short=ts_short)

        # 2. Update Room and Session State
        self._owner = UserManager.get_user(name=owner_name)
        self._session = Session(
            room=self,
            user=user,
            auth_token=auth_token,
            ts_id=ts_id,
            ts_short=ts_short,
            ip=ip,
            conn_time=ts_id,
        )
        self._session.correction_time = int(float(ts_id) - time.time())
        self._flags = RoomFlags(int(flags_str))

        # 3. Initialize Mods
        self._mods = dict()
        if mods_str:
            for mod_record in mods_str.split(";"):
                if "," in mod_record:
                    name, power = mod_record.split(",")
                    mod_user = UserManager.get_user(name=name)
                    mflags = ModeratorFlags(int(power))
                    self._mods[mod_user] = mflags

        self.call_event("ok")

    async def handle_inited(self, _):
        """Signals that first page of chat history has been sent"""
        self.call_event("inited")

    async def handle_gotmore(self, cmd: Command):
        """
        Confirms additional history received.
        Format: gotmore:request_id
        """
        self._gotmore = int(cmd.args[0])
        self.call_event("gotmore")

    async def handle_nomore(self, _):
        """Signals no more pages of history to get via get_more"""
        self._nomore = True
        self.call_event("nomore")

    async def handle_pwdok(self, _):
        """Confirms that the user's authentication credentials were verified"""
        self.call_event("pwdok")

    async def handle_badlogin(self, _):
        """Notifies the client that the authentication attempt failed"""
        self.call_event("badlogin")

    async def handle_badalias(self, _):
        """Notifies the client that the temp name provided was rejected"""
        self.call_event("badalias")

    async def handle_aliasok(self, _):
        """Confirms that the temp name provided was accepted"""
        self.call_event("aliasok")

    async def handle_premium(self, cmd: Command):
        """
        Premium status update.
        Format: premium:status_code:expiry
        """
        args = cmd.args
        code = args[0]
        is_prem = code in ["200", "210"] or (self.owner == self.user)
        self.user.ispremium = is_prem
        if is_prem:
            self.add_task(self.send_command("msgbg", str(self._bgmode or 1)))

    async def handle_annc(self, cmd: Command):
        """
        Broadcast announcement update.
        Format: annc:flags:group_name:message
        """
        args = cmd.args
        flags = int(args[0])
        room = args[1]
        body = ":".join(args[2:])

        # For broadcast, we preserve existing period/delay if we have them
        period = self._announcement.period if self._announcement else None
        delay = self._announcement.message_delay if self._announcement else None

        self._announcement = Room.Announcement(
            flags=flags,
            room_name=room,
            message_raw=body,
            period=period,
            message_delay=delay,
        )

        self.call_event("announcement")

    async def handle_getannc(self, cmd: Command):
        """
        Announcement configuration sync.
        Format: getannc:flags:room:message_delay:period:message
        """
        args = cmd.args
        # Alternate format: getannc:none
        if args[0].lower() == "none":
            self._announcement = Room.Announcement()
        else:
            flags = int(args[0])
            room = args[1]
            delay = int(args[2])
            period = int(args[3])
            body = ":".join(args[4:])

            self._announcement = Room.Announcement(
                flags=flags,
                room_name=room,
                message_raw=body,
                period=period,
                message_delay=delay,
            )
        self.call_event("announcement")

    async def handle_bw(self, cmd: Command):
        """
        Receives the updated list of banned words from the server.
        Format: bw:partially_banned:fully_banned
        """
        args = cmd.args
        part, whole = "", ""
        if args:
            part = urlreq.unquote(args[0])
        if len(args) > 1:
            whole = urlreq.unquote(args[1])
        self._banned_words = (part, whole)
        self.call_event("banned_words")

    async def handle_ubw(self, cmd: Command):
        """
        Informs the client that the banned word list has been updated on the server.
        Format: ubw
        """
        await self.get_banned_words()

    async def handle_getratelimit(self, cmd: Command):
        """
        The server's response to an outgoing getratelimit request.
        Format: getratelimit:limit:seconds_left
        """
        args = cmd.args
        self._rate_limit = int(args[0])
        self.call_event("rate_limit")

    async def handle_ratelimitset(self, cmd: Command):
        """
        Broadcasted by the server when a moderator updates the room's rate limit.
        Format: ratelimitset:limit
        """
        args = cmd.args
        self._rate_limit = int(args[0])
        self.call_event("rate_limit")

    async def handle_n(self, cmd: Command):
        args = cmd.args
        self._usercount = int(args[0], 16)

    async def handle_i(self, cmd: Command):
        """
        Processes historical messages sent by the server during initialization.
        Format: i:TS:SID:TNAME:COOKIE_SHORT:COOKIE_ENC:MSGID:IP:FLAGS:RESERVED:TEXT
        """
        msg = await _process(self, cmd.args)
        self._history.appendleft(msg.id, msg)
        self.call_event("message_history", msg)

    async def handle_b(self, cmd: Command):
        """
        Processes live broadcast messages from the room.
        Format: b:TS:SID:TNAME:COOKIE_SHORT:COOKIE_ENC:MSGID:IP:FLAGS:RESERVED:TEXT
        """
        args = cmd.args
        msg = await _process(self, args)
        if args[5] in self._uqueue:
            msg.id = self._uqueue.pop(args[5])
            self._history.append(msg.id, msg)
            self.call_event("message", msg)
        else:
            self._mqueue[msg.id] = msg

    async def handle_u(self, cmd: Command):
        """
        Binds a temporary message ID to a permanent server-assigned ID.
        Format: u:TEMP_MSG_ID:PERMANENT_MSG_ID
        """
        args = cmd.args
        if args[0] in self._mqueue:
            msg = self._mqueue.pop(args[0])
            msg.id = args[1]
            self._history.append(msg.id, msg)
            self.call_event("message", msg)
        else:
            self._uqueue[args[0]] = args[1]

    async def handle_gparticipants(self, cmd: Command):
        """
        Processes the 'gparticipants' command which provides a full list of
        all participants in the room.

        Format: gparticipants:numAnons:SSID:TIME:COOKIE:NAME:ALIAS:IP;...
        """
        args = cmd.args
        self._anoncount = int(args[0])
        self._userdict = dict()

        # Only anons in chat
        if len(args) == 1:
            self.call_event("participants")
            return

        raw_list = ":".join(args[1:])
        for record in raw_list.split(";"):
            data = record.split(":")

            ssid = data[0]
            contime = data[1]
            cookie = data[2]
            name = data[3] if data[3] != "None" else None
            alias = data[4] if data[4] != "None" else None
            ip = data[5] or None

            ts_short = contime.split(".")[0][-4:].zfill(4)

            if name:
                user = UserManager.get_user(name=name)
            elif alias:
                user = UserManager.get_user(name=alias, aid=cookie, ts_short=ts_short)
            else:
                user = UserManager.get_user(aid=cookie, ts_short=ts_short)

            session = Session(
                user=user,
                room=self,
                ssid=ssid,
                short_cookie=cookie,
                ip=ip,
                conn_time=contime,
            )
            user.add_session(session)
            self._userdict[ssid] = session

            # If this is our own SSID, update our main session
            # Note: We still don't have a foolproof way to know which SSID is ours
            # but if IP and name match, it's a good guess.
            # For now, just ensure we don't overwrite auth_token.

        self.call_event("participants")

    async def handle_participant(self, cmd: Command):
        """
        Processes the 'participant' command which signals a single user
        joining, leaving, or changing authentication status.

        Format: participant:STATUS:SSID:COOKIE:NAME:ALIAS:IP:TIME
        """
        args = cmd.args
        status = args[0]
        ssid = args[1]
        cookie = args[2]
        name = args[3] if args[3] != "None" else None
        alias = args[4] if args[4] != "None" else None
        ip = args[5] or None
        contime = args[6]

        ts_short = contime.split(".")[0][-4:].zfill(4)

        if name:
            user = UserManager.get_user(name=name)
        elif alias:
            user = UserManager.get_user(name=alias, aid=cookie, ts_short=ts_short)
        else:
            user = UserManager.get_user(aid=cookie, ts_short=ts_short)

        session = Session(
            user=user,
            room=self,
            ssid=ssid,
            short_cookie=cookie,
            ip=ip,
            conn_time=contime,
        )
        user.add_session(session)
        self._userdict[ssid] = session

        if status == "0":  # Leave
            self._userdict.pop(ssid)

            if user.isanon:
                if self._anoncount:
                    self._anoncount -= 1

            self.call_event("leave", user)

        elif status == "1":  # Join
            if user.isanon:
                if self._anoncount:
                    self._anoncount += 1

            self.call_event("join", user)

        elif status == "2":  # Auth Change (Login/Logout)
            if name:
                if self._anoncount:
                    self._anoncount -= 1

            if name or alias:
                self.call_event("login", user)
            else:
                if self._anoncount:
                    self._anoncount += 1
                self.call_event("logout", user)

    async def handle_mods(self, cmd: Command):
        args = cmd.args
        pre = self._mods
        mods = self._mods = dict()

        raw_list = ":".join(args)
        if not raw_list:
            if pre:
                user, _ = pre.popitem()
                self.call_event("mod_remove", user)
            return

        for mod_record in raw_list.split(";"):
            if "," in mod_record:
                name, powers = mod_record.split(",", 1)
                utmp = UserManager.get_user(name=name)
                self._mods[utmp] = ModeratorFlags(int(powers))
                self._mods[utmp].isadmin = ModeratorFlags(int(powers)) & AdminFlags != 0

        for user in self.mods - set(pre.keys()):
            self.call_event("mod_added", user)
        for user in set(pre.keys()) - self.mods:
            self.call_event("mod_remove", user)

    async def handle_groupflagsupdate(self, cmd: Command):
        args = cmd.args
        self._flags = RoomFlags(int(args[0]))
        self.call_event("groupflagsupdate")

    async def handle_groupflagstoggled(self, cmd: Command):
        self.call_event("groupflagstoggled")

    async def handle_blocked(self, cmd: Command):
        args = cmd.args
        encoded_cookie = args[0]
        ip = args[1]
        name = args[2]
        moderator = UserManager.get_user(name=args[3])
        time_stamp = float(args[4])

        if name == "":
            msx = [msg for msg in self._history if msg.encoded_cookie == encoded_cookie]
            target = msx[0].user if msx else UserManager.get_user(aid=encoded_cookie)
        else:
            target = UserManager.get_user(name=name)

        self._banlist[target] = self._BANDATA(
            encoded_cookie, ip, target, time_stamp, moderator
        )
        self.call_event("blocked", target, moderator)

    async def handle_blocklist(self, cmd: Command):
        args = cmd.args
        self._banlist = dict()
        sections = ":".join(args).split(";")
        for section in sections:
            params = section.split(":")
            if len(params) != 5:
                continue

            encoded_cookie = params[0]
            ip = params[1]
            name = params[2]
            time_stamp = float(params[3])
            moderator = UserManager.get_user(name=params[4])

            if name == "":
                user = UserManager.get_user(aid=encoded_cookie)
            else:
                user = UserManager.get_user(name=name)

            self._banlist[user] = self._BANDATA(
                encoded_cookie, ip, user, time_stamp, moderator
            )
        self.call_event("blocklist")

    async def handle_unblocked(self, cmd: Command):
        """
        Processes the 'unblocked' command which signals one or more users
        have been unbanned.

        Format: unblocked:COOKIE:IP:NAME;COOKIE:IP:NAME;...
        """
        args = cmd.args
        raw_data = ":".join(args)

        for record in raw_data.split(";"):
            r_parts = record.split(":")
            if len(r_parts) < 3:
                continue

            cookie, ip, name = r_parts[0], r_parts[1], r_parts[2]

            # 1. Resolve Target User
            if name == "":
                target = UserManager.get_user(aid=cookie)
            else:
                target = UserManager.get_user(name=name)

            # 2. Clean up _banlist
            # Search by cookie first
            found = False
            for u, data in list(self._banlist.items()):
                if data.encoded_cookie == cookie:
                    self._banlist.pop(u, None)
                    found = True
                    break

            # Fallback: Search by IP if not found by cookie
            if not found:
                for u, data in list(self._banlist.items()):
                    if data.ip == ip:
                        # For registered users, also match the name
                        if not name or u.name == name.lower():
                            self._banlist.pop(u, None)
                            break

            # 3. Trigger Event (target only)
            self.call_event("unblocked", target)

    async def handle_unblocklist(self, cmd: Command):
        """
        Processes the 'unblocklist' command which provides the history of unbanned users.

        Format: unblocklist:COOKIE:IP:NAME:TIMESTAMP:MODERATOR;...
        """
        args = cmd.args
        raw_data = ":".join(args)
        if not raw_data:
            return

        for record in raw_data.split(";"):
            params = record.split(":")
            if len(params) != 5:
                continue

            cookie = params[0]
            ip = params[1]
            name = params[2]
            time_stamp = float(params[3])
            moderator = UserManager.get_user(name=params[4])

            if name == "":
                target = UserManager.get_user(aid=cookie)
            else:
                target = UserManager.get_user(name=name)

            self._unbanqueue.append(
                self._BANDATA(cookie, ip, target, time_stamp, moderator)
            )

        self.call_event("unblocklist")

    async def handle_clearall(self, cmd: Command):
        args = cmd.args
        self.call_event("clearall", args[0])

    async def handle_denied(self, cmd: Command):
        self.call_event("denied")
        await self.disconnect()

    async def handle_updatemoderr(self, cmd: Command):
        args = cmd.args
        self.call_event("updatemoderr", UserManager.get_user(name=args[1]), args[0])

    async def handle_proxybanned(self, cmd: Command):
        self.call_event("proxybanned")

    async def handle_show_fw(self, cmd: Command):
        self.call_event("show_fw")

    async def handle_show_tb(self, cmd: Command):
        args = cmd.args
        self.call_event("show_tb", int(args[0]))

    async def handle_tb(self, cmd: Command):
        """Temporary ban sigue activo con el tiempo indicado"""
        args = cmd.args
        self.call_event("temp_ban", int(args[0]))

    async def handle_updgroupinfo(self, cmd: Command):
        await self.load_profile()
        # load_profile calls "profile" event

    async def handle_miu(self, cmd: Command):
        args = cmd.args
        user = UserManager.get_user(name=args[0])
        await user.load_resources()
        self.call_event("miu", user)

    async def handle_delete(self, cmd: Command):
        """
        Broadcast message deletion.
        Format: delete:msg_id
        """
        msg_id = cmd.args[0]
        self._delete_history(msg_id)
        self.call_event("delete", msg_id)

    async def handle_deleteall(self, cmd: Command):
        """
        Broadcast deletion of all messages from a user.
        Format: deleteall:msg_id1:msg_id2:...
        """
        msg_ids = cmd.args
        for msg_id in msg_ids:
            self._delete_history(msg_id)
        self.call_event("deleteall", msg_ids)

    async def handle_ratelimited(self, cmd: Command):
        args = cmd.args
        wait_time = int(args[0])
        self.call_event("ratelimited", wait_time)

    async def handle_msglexceeded(self, cmd: Command):
        self.call_event("msglexceeded")

    async def handle_climited(self, cmd: Command):
        self.call_event("climited")

    async def handle_show_nlp(self, cmd: Command):
        self.call_event("show_nlp")

    async def handle_nlptb(self, cmd: Command):
        self.call_event("nlptb")

    async def handle_logoutfirst(self, cmd: Command):
        self.call_event("logoutfirst")

    async def handle_logoutok(self, cmd: Command):
        """
        Processes the 'logoutok' command which signals that the user has
        successfully logged out and reverted to anonymous status.
        """
        if self._session:
            # Revert to anonymous status using the cookie (aid) from the session
            new_user = UserManager.get_user(
                aid=self._session.short_cookie, ip=self._session.ip
            )

            self._session.user = new_user
        self.call_event("logoutok")

    async def handle_updateprofile(self, cmd: Command):
        args = cmd.args
        user = UserManager.get_user(name=args[0])
        await user.load_resources()
        self.call_event("updateprofile", user)

    # --- Documented Protocol Stubs ---

    async def handle_cbw(self, cmd: Command):
        self.call_event("cbw")

    async def handle_end_fw(self, cmd: Command):
        self.call_event("end_fw")

    async def handle_show_nlp_tb(self, cmd: Command):
        self.call_event("show_nlp_tb")

    async def handle_end_nlp(self, cmd: Command):
        self.call_event("end_nlp")

    async def handle_notifysettings(self, cmd: Command):
        self.call_event("notifysettings")

    async def handle_setnotifysettings(self, cmd: Command):
        self.call_event("setnotifysettings")

    async def handle_checkemail_notify(self, cmd: Command):
        self.call_event("checkemail_notify")

    async def handle_addmoderr(self, cmd: Command):
        self.call_event("addmoderr")

    async def handle_removemoderr(self, cmd: Command):
        self.call_event("removemoderr")

    async def handle_modactions(self, cmd: Command):
        self.call_event("modactions")

    async def handle_mustlogin(self, cmd: Command):
        self.call_event("mustlogin")

    async def handle_chatango(self, cmd: Command):
        self.call_event("chatango")

    async def handle_limitexceeded(self, cmd: Command):
        self.call_event("limitexceeded")

    async def handle_verificationrequired(self, cmd: Command):
        self.call_event("verificationrequired")

    async def handle_verificationchanged(self, cmd: Command):
        self.call_event("verificationchanged")

    async def handle_versioningPU(self, cmd: Command):
        self.call_event("versioningPU")

    async def handle_badbansearchstring(self, cmd: Command):
        self.call_event("badbansearchstring")

    async def handle_bansearchresult(self, cmd: Command):
        self.call_event("bansearchresult")

    async def handle_allunblocked(self, cmd: Command):
        self.call_event("allunblocked")
