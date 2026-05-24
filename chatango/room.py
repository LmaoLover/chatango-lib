import asyncio
import html
import time
import enum
import re
import logging
import urllib.parse as urlreq

from collections import deque, namedtuple
from typing import Optional

from .utils import (
    get_server,
    _id_gen,
    public_attributes,
)
from .message import MessageFlags, RoomMessage, _process, message_cut, Command
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
        "gparticipants": "gparticipants",
        "updategroupflags": "groupflagstoggled",
    }

    @classmethod
    def assert_valid_name(cls, room_name: str):
        if not Room.valid_name.match(room_name):
            raise InvalidRoomNameError(room_name)

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
        self._mqueue = dict()
        self._uqueue = dict()
        self._messages = dict()
        self._history = deque(maxlen=3000)
        self._mods = dict()
        self._userdict = dict()
        self._banlist = dict()
        self._unbanlist = dict()
        self._unbanqueue = deque(maxlen=500)
        self._usercount: Optional[int] = None
        self._anoncount: Optional[int] = None
        self._maxlen = 2800
        self._bgmode = 0
        self._nomore = False
        self._profile = RoomProfile()
        self._banned_words = ("", "")
        self._announcement = [0, 0, ""]
        self._rate_limit = 0

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
        return self._profile

    async def load_profile(self):
        """Fetches the group profile resource and updates instance."""
        results = await fetch_resources(self.name, [RoomProfile])
        if results:
            self._profile = results[0]

    async def save_profile(self, password: str):
        """Saves current room profile properties to the server via class method."""
        handle = self.user.name
        if not handle:
            return False
        return await RoomProfile.save(self.name, password, self.profile)

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
    def messages(self):
        return self._messages

    @property
    def history(self):
        return self._history

    @property
    def description(self):
        return self.profile.group_body_html

    @property
    def title(self):
        return self.profile.group_title

    @property
    def banlist(self):
        return list(self._banlist.keys())

    @property
    def rate_limit(self):
        return self._rate_limit

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
            await self._request_initial_data()
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
        elif result.name == "aliasok" and self.session.short_cookie:
            self.session.user = UserManager.get_user(
                name=user_name, aid=self.session.short_cookie
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

    def get_last_message(self, user=None) -> Optional[RoomMessage]:
        if not self._history:
            return None
        if not user:
            return self._history[-1]
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        for msg in reversed(self.history):
            if msg.user == user:
                return msg
        return None

    async def _raw_unban(self, name, ip, encoded_cookie):
        await self.send_command("removeblock", encoded_cookie, ip, name)

    def _add_history(self, msg):
        if len(self._history) == self.history.maxlen:
            rest = self._history.popleft()
            self._messages.pop(rest.id)
        self._history.append(msg)
        self._messages[msg.id] = msg

    def _add_history_left(self, msg):
        # Add older history unless full
        if self.history.maxlen and len(self._history) < self.history.maxlen:
            self._history.appendleft(msg)
            self._messages[msg.id] = msg

    def _remove_history(self, msgid):
        msg = self._messages.pop(msgid, None)
        if msg and msg in self._history:
            self._history.remove(msg)
        return msg

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

    async def clear_all(self):
        """Borra todos los mensajes"""
        if (
            self.user in self._mods
            and ModeratorFlags.EDIT_GROUP in self._mods[self.user]
            or self.user == self.owner
        ):
            await self.send_command("clearall")
            return True
        else:
            return False

    async def clear_user(self, user):
        # TODO
        if self.get_level(self.user) > 0:
            msg = self.get_last_message(user)
            if msg:
                name = "" if msg.user.isanon else msg.user.name
                await self.send_command("delallmsg", msg.encoded_cookie, msg.ip, name)
                return True
        return False

    async def delete_message(self, message):
        if self.get_level(self.user) > 0 and message.id:
            await self.send_command("delmsg", message.id)
            return True
        return False

    async def delete_user(self, user):
        if self.get_level(self.user) > 0:
            msg = self.get_last_message(user)
            if msg:
                await self.delete_message(msg)
        return False

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

    async def _request_initial_data(self):
        """Requests initial state data from server."""
        await self.send_command("getpremium", "l")
        await self.send_command("getannouncement")
        await self.send_command("getbannedwords")
        await self.send_command("getratelimit")
        await self.request_banlist()
        await self.request_unbanlist()
        if self.user.ispremium:
            await self._style_init(self.user)

    async def request_participants(self):
        """Enables participant mode."""
        await self.send_command("gparticipants")

    async def stop_participants(self):
        """Disables participant mode."""
        await self.send_command("gparticipants", "stop")

    async def get_premium_info(self):
        """Sequential workflow to request and return premium status."""
        return await self.send_command("getpremium", "l", expect="premium")

    async def get_announcement(self):
        """Sequential workflow to request and return the current announcement."""
        return await self.send_command("getannouncement", expect="getannc")

    async def set_bg_mode(self, mode):
        self._bgmode = mode
        if self.connected:
            await self.send_command("getpremium", "l")
            if self.user.ispremium:
                await self.send_command("msgbg", str(self._bgmode))

    async def _style_init(self, user):
        if not user.isanon:
            await user.load_resources()
        else:
            self.set_font(
                name_color="000000", font_color="000000", font_size=11, font_face=1
            )

    def _process_premium_state(self, cmd: Command):
        args = cmd.args
        code = args[0]
        is_prem = code in ["200", "210"] or (self.owner == self.user)
        self.user.ispremium = is_prem
        if is_prem:
            self.add_task(self.send_command("msgbg", str(self._bgmode or 1)))

    def _process_announcement_state(self, cmd: Command, from_get=False):
        """Helper to process announcement data from both 'annc' and 'getannc'."""
        args = cmd.args
        if from_get:
            # getannc: enabled(0), room(1), ?(2), periodicity(3), message(4+)
            if len(args) < 4 or args[0].lower() == "none":
                return
            enabled = int(args[0])
            period = int(args[3])
            body = ":".join(args[4:])
        else:
            # annc: flags(0), group_name(1), message(2+)
            enabled = int(args[0])
            period = 0  # Not provided in broadcast
            body = ":".join(args[2:])

        if body != self._announcement[2]:
            self._announcement = [enabled, period, body]
            self.call_event("announcement_update", enabled != 0)
        self.call_event("announcement", body)

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

        await self.load_profile()
        self.call_event("ok")

    async def handle_inited(self, cmd: Command):
        self.call_event("inited")

    async def handle_pwdok(self, cmd: Command):
        await self.send_command("getpremium", "l")
        await self._style_init(self.user)

    async def handle_annc(self, cmd: Command):
        self._process_announcement_state(cmd, from_get=False)

    async def handle_nomore(self, cmd: Command):  # TODO
        """No more past messages"""
        pass

    async def handle_n(self, cmd: Command):
        args = cmd.args
        self._usercount = int(args[0], 16)

    async def handle_i(self, cmd: Command):
        """history past messages"""
        args = cmd.args
        msg = await _process(self, args)
        self._add_history_left(msg)

    async def handle_b(self, cmd: Command):
        args = cmd.args
        msg = await _process(self, args)
        if args[5] in self._uqueue:
            msg.id = self._uqueue.pop(args[5])
            self._add_history(msg)
            self.call_event("message", msg)
        else:
            self._mqueue[msg.id] = msg

    async def handle_premium(self, cmd: Command):
        self._process_premium_state(cmd)

    async def handle_u(self, cmd: Command):
        args = cmd.args
        if args[0] in self._mqueue:
            msg = self._mqueue.pop(args[0])
            msg.id = args[1]
            self._add_history(msg)
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
        self.call_event("updgroupinfo")

    async def handle_miu(self, cmd: Command):
        args = cmd.args
        user = UserManager.get_user(name=args[0])
        await user.load_resources()
        self.call_event("miu", user)

    async def handle_delete(self, cmd: Command):
        """Borrar un mensaje de mi vista actual"""
        args = cmd.args
        msg = self._remove_history(args[0])
        if msg:
            self.call_event("delete", msg)
        #
        if len(self._history) < 20 and not self._nomore:
            await self.send_command("get_more", "20", "0")

    async def handle_deleteall(self, cmd: Command):
        """Mensajes han sido borrados"""
        args = cmd.args
        msgs_nones = [self._remove_history(msgid) for msgid in args]
        msgs = [msg for msg in msgs_nones if msg]
        if msgs:
            self.call_event("deleteall", msgs)

    # Receive banned word lists from server
    async def handle_bw(self, cmd: Command):
        args = cmd.args
        part, whole = "", ""
        if args:
            part = urlreq.unquote(args[0])
        if len(args) > 1:
            whole = urlreq.unquote(args[1])
        self._banned_words = (part, whole)
        self.call_event("bw")

    async def handle_getannc(self, cmd: Command):
        self._process_announcement_state(cmd, from_get=True)

    async def handle_getratelimit(self, cmd: Command):
        args = cmd.args
        self._rate_limit = int(args[0])
        self.call_event("ratelimitset")

    async def handle_ratelimitset(self, cmd: Command):
        args = cmd.args
        self._rate_limit = int(args[0])
        self.call_event("ratelimitset")

    async def handle_ratelimited(self, cmd: Command):
        args = cmd.args
        wait_time = int(args[0])
        self.call_event("ratelimited", wait_time)

    async def handle_msglexceeded(self, cmd: Command):
        self.call_event("msglexceeded")

    # Server updated banned words
    async def handle_ubw(self, cmd: Command):
        await self.send_command("getbannedwords")

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

    async def handle_gotmore(self, cmd: Command):
        self.call_event("gotmore")

    async def handle_mustlogin(self, cmd: Command):
        self.call_event("mustlogin")

    async def handle_badlogin(self, cmd: Command):
        self.call_event("badlogin")

    async def handle_badalias(self, cmd: Command):
        self.call_event("badalias")

    async def handle_aliasok(self, cmd: Command):
        self.call_event("aliasok")

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
