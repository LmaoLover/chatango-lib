import aiohttp
import asyncio
import socket
import html
import time
import enum
import re
import logging
import urllib.request as urlreq

from collections import deque, namedtuple
from typing import Optional

from .utils import (
    get_aiohttp_session,
    get_server,
    _id_gen,
    public_attributes,
)
from .message import MessageFlags, RoomMessage, _process, message_cut
from .user import User, ModeratorFlags, AdminFlags, UserManager, Session
from .resources import RoomProfile, fetch_resources
from .exceptions import AlreadyConnectedError, InvalidRoomNameError
from .handler import CommandHandler, EventHandler

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


class WebsocketConnection(CommandHandler):
    def __init__(self):
        super().__init__()
        self._reset()

    def _reset(self):
        self._connected = False
        self._connection: Optional[aiohttp.ClientWebSocketResponse] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    @property
    def connected(self):
        return (
            self._connected
            and self._connection is not None
            and not self._connection.closed
        )

    async def _connect(self, url: str):
        try:
            self._connection = await get_aiohttp_session().ws_connect(
                url, origin="http://st.chatango.com"
            )
            self._connected = True
            self._recv_task = asyncio.create_task(self._do_recv())
            self._ping_task = asyncio.create_task(self._do_ping())
            logger.info(f"WebSocket connected to {url}")

        except (ValueError, TypeError, aiohttp.InvalidURL) as e:
            await self._disconnect()
            logger.critical(f"Invalid configuration for {url}: {e}")
            raise

        except (aiohttp.ClientConnectorSSLError, aiohttp.ClientSSLError) as e:
            await self._disconnect()
            logger.critical(f"SSL configuration error for {url}: {e}")
            raise

        except aiohttp.WSServerHandshakeError as e:
            await self._disconnect()
            error_str = str(e).lower()
            if (
                "404" in error_str and "websocket" in error_str
            ) or "upgrade required" in error_str:
                logger.error(f"WebSocket configuration error for {url}: {e}")
                raise
            else:
                logger.warning(f"WebSocket handshake failed for {url}: {e}")
                return

        except aiohttp.ClientResponseError as e:
            await self._disconnect()
            if e.status == 404 and "websocket" in str(e).lower():
                logger.error(f"WebSocket endpoint not found for {url}: {e.message}")
                raise
            else:
                logger.warning(f"HTTP error {e.status} for {url}: {e.message}")
                return

        except socket.gaierror as e:
            await self._disconnect()
            # DNS errors - retry most of them since DNS can be flaky
            host = url.split("//")[-1].split(":")[0]
            if (
                e.errno == socket.EAI_NONAME
                and not host.replace(".", "").replace("-", "").isalnum()
            ):
                logger.error(f"Invalid hostname {host}: {e}")
                raise
            else:
                logger.warning(f"DNS resolution failed for {url}: {e}")
                return

        except (
            ConnectionResetError,
            ConnectionRefusedError,
            ConnectionAbortedError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
            aiohttp.ClientPayloadError,
        ) as e:
            await self._disconnect()
            logger.warning(f"Temporary connection failure for {url}: {e}")

        except aiohttp.ClientConnectorError as e:
            await self._disconnect()
            error_str = str(e).lower()
            if any(
                k in error_str
                for k in [
                    "name or service not known",
                    "nodename nor servname provided",
                    "no address associated with hostname",
                ]
            ):
                logger.error(f"Invalid hostname for {url}: {e}")
                raise
            else:
                logger.warning(f"Network connectivity issue for {url}: {e}")
                return

        except aiohttp.ClientError as e:
            await self._disconnect()
            logger.warning(f"Client error for {url}: {e}")
            return

        except Exception as e:
            await self._disconnect()
            logger.warning(f"Unexpected error connecting to {url}: {e}")
            return

    async def _disconnect(self):
        self._connected = False
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass
        self._cancel_all_pending_futures()
        self._reset()
        logger.info("WebSocket disconnected")

    async def _send_command(self, command: str, terminator: str = "\r\n\0"):
        if not self.connected:
            logger.error(f'Message send failed "{command}": Not connected')
            return
        try:
            await self._connection.send_str(command + terminator)
        except Exception as e:
            logger.error(f'Message send failed "{command}": {e}')

    async def _do_ping(self):
        """
        Ping the socket every minute to keep alive
        """
        try:
            while self.connected:
                await asyncio.sleep(90)
                if self.connected:
                    await self._send_command("\r\n", terminator="\x00")
        except asyncio.CancelledError:
            pass

    async def _do_recv(self):
        """
        Receives data from the websocket. When this task finishes, it signals
        that the connection is effectively dead.
        """
        try:
            while self.connected:
                try:
                    message = await self._connection.receive()
                except Exception as e:
                    logger.error(f"Exception during receive, closing connection: {e}")
                    break

                if message.type == aiohttp.WSMsgType.TEXT:
                    if message.data:
                        # Chatango often sends multiple commands in one packet,
                        # delimited by null bytes or newlines.
                        raw_data = message.data
                        # Standard Chatango delimiter is \x00, sometimes \r\n\x00
                        cmds = raw_data.split("\x00")
                        for cmd in cmds:
                            clean_cmd = cmd.strip("\r\n")
                            if clean_cmd:
                                await self._receive_command(clean_cmd)
                elif message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
                else:
                    logger.error(f"Unexpected aiohttp.WSMsgType: {message.type}")
        except asyncio.CancelledError:
            pass


class Room(WebsocketConnection, EventHandler):
    _BANDATA = namedtuple("BanData", ["encoded_cookie", "ip", "target", "time", "src"])

    def __dir__(self):
        return public_attributes(self)

    def __init__(self, name: str):
        WebsocketConnection.__init__(self)
        EventHandler.__init__(self)
        self.assert_valid_name(name)
        self.name = name
        self.server = get_server(name)
        self._profile = RoomProfile()
        self.reconnect = False
        self.owner: Optional[User] = None
        self._banned_words = ("", "")
        self.session: Session = Session(room=self, user=UserManager.get_user())
        self._silent = False
        self._mods = dict()
        self._userdict = dict()
        self._mqueue = dict()
        self._uqueue = dict()
        self._messages = dict()
        self._history = deque(maxlen=3000)
        self._banlist = dict()
        self._unbanlist = dict()
        self._unbanqueue = deque(maxlen=500)
        self._usercount = 0
        self._maxlen = 2800
        self._bgmode = 0
        self._nomore = False
        self.message_flags = 0
        self._announcement = [0, 0, ""]
        self._rate_limit = 0
        self._flags: Optional[RoomFlags] = None

    def __repr__(self):
        return f"<Room {self.name}>"

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
        badge_val = self.session.badge

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
    def silent(self):
        return self._silent

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
    def flags(self):
        return self._flags

    @property
    def rate_limit(self):
        return self._rate_limit

    @property
    def user(self):
        return self.session.user

    @property
    def mods(self):
        return set(self._mods.keys())

    @property
    def userlist(self):
        return self._get_user_list()

    @property
    def anonlist(self):
        """Lista de anons detectados"""
        return list(set(self.alluserlist) - set(self.userlist))

    @property
    def usercount(self):  # TODO
        """Len users -> user count"""
        if self._flags and RoomFlags.NO_COUNTER in self._flags:
            return len(self.alluserlist)
        return self._usercount

    @property
    def alluserlist(self):
        """Lista de todos los usuarios en la sala (con anons)"""
        return sorted([s.user for s in self._userdict.values()], key=lambda z: z.name)

    @classmethod
    def assert_valid_name(cls, room_name: str):
        expr = re.compile("^([a-z0-9-]{1,20})$")
        if not expr.match(room_name):
            raise InvalidRoomNameError(room_name)

    async def connect(self, user_name: str = "", password: str = ""):
        """
        The complete handshake workflow written sequentially.
        """
        if self.connected:
            raise AlreadyConnectedError(self.name)

        await self._connect(f"wss://{self.server}:8081/")

        try:
            await self.send_command("v", expect="v", timeout=5.0)

            ok_args = await self._auth(user_name, password, expect="ok", timeout=10.0)
            await self._rcmd_ok(ok_args)
            self.call_event("ready")

            if self.user.isanon:
                await self.send_command("msgbg", "0")

            await self._request_initial_data()

            logger.info(f"Successfully connected and initialized {self.name}")

        except (TimeoutError, ConnectionError) as e:
            logger.error(f"Handshake failed for {self.name}: {e}")
            await self.disconnect()
            raise

    async def connection_wait(self):
        """
        Wait until the connection is closed
        """
        self.call_event("connect")
        if self._recv_task:
            await self._recv_task
        self.call_event("disconnect")

    async def disconnect(self):
        """
        Force this room to disconnect
        """
        self._userdict.clear()
        self.reconnect = False
        await self._disconnect()

    async def bounce(self):
        """
        Disconnect but allow reconnection
        """
        await self._disconnect()

    async def listen(self, user_name: str = "", password: str = "", reconnect=False):
        """
        Join and wait on room connection
        """
        self.reconnect = reconnect
        while True:
            try:
                await self.connect(user_name, password)
                await self.connection_wait()
            finally:
                await self._disconnect()
            if not self.reconnect:
                break
            await asyncio.sleep(3)
        self.end_tasks()

    async def _auth(self, user_name: str = "", password: str = "", **kwargs):
        """
        Login when joining a room
        """
        if user_name:
            self.session.user = UserManager.get_user(name=user_name)
        return await self.send_command(
            "bauth",
            self.name,
            self.session.session_id or "",
            user_name,
            password,
            **kwargs,
        )

    async def _login(self, user_name: str = "", password: str = "", **kwargs):
        """
        Login after having connected as anon
        """
        if password:
            self.session.user = UserManager.get_user(name=user_name)
        else:
            self.session.user = UserManager.get_user(
                name=user_name, aid=self.session.short_cookie
            )
        return await self.send_command("blogin", user_name, password, **kwargs)

    async def _logout(self):
        await self.send_command("blogout")

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
                styled_msg = f"{self.user.styles.get_name_tag(is_anon)}{self.user.styles.format_message(msg, is_anon=is_anon)}"
                await self.send_command("bm", _id_gen(), str(message_flags), styled_msg)

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

    def _get_user_list(self, unique=1, memory=0, anons=False):
        ul = []
        if not memory:
            ul = [s.user for s in self._userdict.values() if anons or not s.user.isanon]
        elif type(memory) == int:
            ul = set(
                map(
                    lambda x: x.user,
                    list(self._history)[min(-memory, len(self._history)) :],
                )
            )
        if unique:
            ul = set(ul)
        return sorted(list(ul), key=lambda x: x.name)

    def get_level(self, user):
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        if user == self.owner:
            return 3
        if user in self._mods:
            if self._mods.get(user).isadmin:
                return 2
            else:
                return 1
        return 0

    def ban_record(self, user):
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        return self._banlist.get(user)

    def get_last_message(self, user=None):
        """Obtener el último mensaje de un usuario en una sala"""
        if not user:
            return self._history and self._history[-1] or None
        if isinstance(user, str):
            user = UserManager.get_user(name=user)
        for x in reversed(self.history):
            if x.user == user:
                return x
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
                "setbannedwords", urlreq.quote(part), urlreq.quote(whole)
            )
            return True
        return False

    async def _reload(self):
        """Requests initial state data from server without waiting for response."""
        await self.send_command("getpremium", "l")
        await self.send_command("getannouncement")
        await self.send_command("getbannedwords")
        await self.send_command("getratelimit")
        await self.request_banlist()
        await self.request_unbanlist()
        if self.user.ispremium:
            await self._style_init(self.user)

    async def _request_initial_data(self):
        """Requests initial state data from server."""
        await self._reload()

    async def request_participants(self):
        """Enables participant mode."""
        await self.send_command("gparticipants")

    async def stop_participants(self):
        """Disables participant mode."""
        await self.send_command("gparticipants", "stop")

    async def get_premium_info(self):
        """Sequential workflow to request and return premium status."""
        return await self.send_command("getpremium", "l", expect="premium", timeout=5.0)

    async def get_announcement(self):
        """Sequential workflow to request and return the current announcement."""
        return await self.send_command("getannouncement", expect="getannc", timeout=5.0)

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

    def _process_premium_state(self, args):
        code = args[0]
        is_prem = code in ["200", "210"] or (self.owner == self.user)
        self.user.ispremium = is_prem
        if is_prem:
            self.add_task(self.send_command("msgbg", str(self._bgmode or 1)))

    def _process_announcement_state(self, args, from_get=False):
        """Helper to process announcement data from both 'annc' and 'getannc'."""
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

    async def _rcmd_ok(self, args):
        """
        Processes the 'ok' command which signals successful connection and
        provides room/user metadata.

        Format: ok:OWNER:COOKIE:LOGIN_AS:CURRENT_NAME:CONN_TIME:IP:MODS:FLAGS
        """
        if len(args) < 8:
            return

        owner_name = args[0]
        session_id = args[1]
        login_status = args[2]
        current_name = args[3]
        ts_id = args[4]
        ip = args[5]
        mods_str = args[6]
        flags_str = args[7]

        # 1. Resolve current User
        if login_status == "M":
            user = UserManager.get_user(name=current_name)
            await self._style_init(user)
        else:
            # Guest: Create a stub user, identity will be discovered in gparticipants
            user = UserManager.get_user()

        # 2. Update Room and Session State
        self.owner = UserManager.get_user(name=owner_name)
        self.session.user = user
        self.session.session_id = session_id
        self.session.ts_id = ts_id
        self.session.ip = ip
        self.session.conn_time = ts_id
        self.session.correction_time = int(float(ts_id) - time.time())
        user.addSession(self.session)
        self._userdict[session_id] = self.session

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

    async def _rcmd_inited(self, args):
        self.call_event("inited")

    async def _rcmd_pwdok(self, args):
        await self.send_command("getpremium", "l")
        await self._style_init(self.user)

    async def _rcmd_annc(self, args):
        self._process_announcement_state(args, from_get=False)

    async def _rcmd_nomore(self, args):  # TODO
        """No more past messages"""
        pass

    async def _rcmd_n(self, args):
        """user count"""
        self._usercount = int(args[0], 16)

    async def _rcmd_i(self, args):
        """history past messages"""
        msg = await _process(self, args)
        self._add_history_left(msg)

    async def _rcmd_b(self, args):
        msg = await _process(self, args)
        if args[5] in self._uqueue:
            msg.id = self._uqueue.pop(args[5])
            self._add_history(msg)
            self.call_event("message", msg)
        else:
            self._mqueue[msg.id] = msg

    async def _rcmd_premium(self, args):
        self._process_premium_state(args)

    async def _rcmd_u(self, args):
        if args[0] in self._mqueue:
            msg = self._mqueue.pop(args[0])
            msg.id = args[1]
            self._add_history(msg)
            self.call_event("message", msg)
        else:
            self._uqueue[args[0]] = args[1]

    async def _rcmd_gparticipants(self, args):
        """
        Processes the 'gparticipants' command which provides a full list of
        all participants in the room.

        Format: gparticipants:numAnons:SESSIONID:TIME:COOKIE:NAME:ALIAS:IP;...
        """
        self._userdict = dict()
        if not args:
            return

        # args[0] is numAnons
        self._usercount = int(args[0])

        raw_list = ":".join(args[1:])
        if not raw_list:
            self.call_event("participants")
            return

        for record in raw_list.split(";"):
            data = record.split(":")
            if len(data) < 6:
                continue

            ssid = data[0]
            contime = data[1]
            cookie = data[2]
            name = data[3]
            alias = data[4]
            ip = data[5]

            is_anon = name == "None"
            is_temp = is_anon and alias != "None"

            if not is_anon:
                # Registered user: identified by name
                user = UserManager.get_user(name=name)
            elif is_temp:
                # Temporary user: identified by cookie (aid), display alias
                user = UserManager.get_user(name=alias, aid=cookie)
            else:
                # Anonymous user: identified by cookie (aid)
                user = UserManager.get_user(aid=cookie)

            # Identity Discovery: If this is our connection, resolve our full identity
            if ssid == self.session.session_id:
                self.session.short_cookie = cookie
                self.session.ip = ip
                self.session.user = user
                session = self.session
            else:
                session = Session(
                    user=user,
                    room=self,
                    session_id=ssid,
                    short_cookie=cookie,
                    ip=ip,
                    conn_time=contime,
                )

            user.addSession(session)
            self._userdict[ssid] = session

        self.call_event("participants")

    async def _rcmd_participant(self, args):
        """
        Processes the 'participant' command which signals a single user
        joining, leaving, or changing authentication status.

        Format: participant:STATUS:SESSIONID:COOKIE:NAME:ALIAS:IP:TIME
        """
        if len(args) < 7:
            return

        status = args[0]  # 0=Leave, 1=Join, 2=Auth Change
        ssid = args[1]
        cookie = args[2]
        name = args[3]
        alias = args[4]
        ip = args[5]
        contime = args[6]

        is_anon = name == "None"
        is_temp = is_anon and alias != "None"

        if not is_anon:
            # Registered user: identified by name
            user = UserManager.get_user(name=name)
        elif is_temp:
            # Temporary user: identified by cookie (aid), display alias
            user = UserManager.get_user(name=alias, aid=cookie)
        else:
            # Anonymous user: identified by cookie (aid)
            user = UserManager.get_user(aid=cookie)

        before_session = self._userdict.get(ssid)
        before = before_session.user if before_session else None

        if status == "0":  # Leave
            if before_session:
                before.removeSession(before_session)
            if ssid in self._userdict:
                self._userdict.pop(ssid)

            if user.isanon:
                self.call_event("anon_leave", user)
            else:
                self.call_event("leave", user)

        elif status == "1" or not before:  # Join
            session = Session(
                user=user,
                room=self,
                session_id=ssid,
                short_cookie=cookie,
                ip=ip,
                conn_time=contime,
            )
            user.addSession(session)
            self._userdict[ssid] = session

            if user.isanon:
                self.call_event("anon_join", user)
            else:
                self.call_event("join", user)

        elif status == "2":  # Auth Change (Login/Logout)
            if before_session:
                before.removeSession(before_session)
            session = Session(
                user=user,
                room=self,
                session_id=ssid,
                short_cookie=cookie,
                ip=ip,
                conn_time=contime,
            )
            user.addSession(session)
            self._userdict[ssid] = session

            if before and before.isanon:  # Login
                if user.isanon:
                    self.call_event("anon_login", before, user)
                else:
                    self.call_event("user_login", before, user)
            elif before:  # Logout
                self.call_event("user_logout", before, user)

    async def _rcmd_mods(self, args):
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

    async def _rcmd_groupflagsupdate(self, args):
        flags = args[0]
        self._flags = RoomFlags(int(flags))
        self.call_event("groupflagsupdate")

    async def _rcmd_blocked(self, args):
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

    async def _rcmd_blocklist(self, args):
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

    async def _rcmd_unblocked(self, args):
        """
        Processes the 'unblocked' command which signals one or more users
        have been unbanned.

        Format: unblocked:COOKIE:IP:NAME;COOKIE:IP:NAME;...
        """
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

    async def _rcmd_unblocklist(self, args):
        """
        Processes the 'unblocklist' command which provides the history of unbanned users.

        Format: unblocklist:COOKIE:IP:NAME:TIMESTAMP:MODERATOR;...
        """
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

    async def _rcmd_clearall(self, args):
        self.call_event("clearall", args[0])

    async def _rcmd_denied(self, args):
        self.call_event("denied")
        await self.disconnect()

    async def _rcmd_updatemoderr(self, args):
        self.call_event("updatemoderr", UserManager.get_user(name=args[1]), args[0])

    async def _rcmd_proxybanned(self, args):
        self.call_event("proxybanned")

    async def _rcmd_show_fw(self, args):
        self.call_event("show_fw")

    async def _rcmd_show_tb(self, args):
        self.call_event("show_tb", int(args[0]))

    async def _rcmd_tb(self, args):
        """Temporary ban sigue activo con el tiempo indicado"""
        self.call_event("temp_ban", int(args[0]))

    async def _rcmd_updgroupinfo(self, args):
        await self.load_profile()
        self.call_event("updgroupinfo")

    async def _rcmd_miu(self, args):
        user = UserManager.get_user(name=args[0])
        await user.load_resources()
        self.call_event("miu", user)

    async def _rcmd_delete(self, args):
        """Borrar un mensaje de mi vista actual"""
        msg = self._remove_history(args[0])
        if msg:
            self.call_event("delete", [msg])
        #
        if len(self._history) < 20 and not self._nomore:
            await self.send_command("get_more", "20", "0")

    async def _rcmd_deleteall(self, args):
        """Mensajes han sido borrados"""
        msgs_nones = [self._remove_history(msgid) for msgid in args]
        msgs = [msg for msg in msgs_nones if msg]
        if msgs:
            self.call_event("delete", msgs)

    # Receive banned word lists from server
    async def _rcmd_bw(self, args):
        part, whole = "", ""
        if args:
            part = urlreq.unquote(args[0])
        if len(args) > 1:
            whole = urlreq.unquote(args[1])
        self._banned_words = (part, whole)
        self.call_event("bw")

    async def _rcmd_getannc(self, args):
        self._process_announcement_state(args, from_get=True)

    async def _rcmd_getratelimit(self, args):
        self._rate_limit = int(args[0])
        self.call_event("ratelimitset")

    async def _rcmd_ratelimitset(self, args):
        self._rate_limit = int(args[0])
        self.call_event("ratelimitset")

    async def _rcmd_ratelimited(self, args):
        wait_time = int(args[0])
        self.call_event("ratelimited", wait_time)

    async def _rcmd_msglexceeded(self, args):
        self.call_event("msglexceeded")

    # Server updated banned words
    async def _rcmd_ubw(self, args):
        await self.send_command("getbannedwords")

    async def _rcmd_climited(self, args):
        self.call_event("climited")

    async def _rcmd_show_nlp(self, args):
        self.call_event("show_nlp")

    async def _rcmd_nlptb(self, args):
        self.call_event("nlptb")

    async def _rcmd_logoutfirst(self, args):
        self.call_event("logoutfirst")

    async def _rcmd_logoutok(self, args):
        """
        Processes the 'logoutok' command which signals that the user has
        successfully logged out and reverted to anonymous status.
        """
        if not self.session:
            return

        # Revert to anonymous status using the cookie (aid) from the session
        new_user = UserManager.get_user(
            aid=self.session.short_cookie, ip=self.session.ip
        )

        self.session.user = new_user
        self.call_event("logoutok", new_user)

    async def _rcmd_updateprofile(self, args):
        """Cuando alguien actualiza su perfil en un chat"""
        user = UserManager.get_user(name=args[0])
        user._profile = None
        self.call_event("profile_changes", user)

    async def _rcmd_reload_profile(self, args):
        user = UserManager.get_user(name=args[0])
        user._profile = None
        self.call_event("profile_reload", user)

    # --- Documented Protocol Stubs ---

    async def _rcmd_groupflagstoggled(self, args):
        self.call_event("groupflagstoggled")

    async def _rcmd_cbw(self, args):
        self.call_event("cbw")

    async def _rcmd_end_fw(self, args):
        self.call_event("end_fw")

    async def _rcmd_show_nlp_tb(self, args):
        self.call_event("show_nlp_tb")

    async def _rcmd_end_nlp(self, args):
        self.call_event("end_nlp")

    async def _rcmd_notifysettings(self, args):
        self.call_event("notifysettings")

    async def _rcmd_setnotifysettings(self, args):
        self.call_event("setnotifysettings")

    async def _rcmd_checkemail_notify(self, args):
        self.call_event("checkemail_notify")

    async def _rcmd_addmoderr(self, args):
        self.call_event("addmoderr")

    async def _rcmd_removemoderr(self, args):
        self.call_event("removemoderr")

    async def _rcmd_modactions(self, args):
        self.call_event("modactions")

    async def _rcmd_gotmore(self, args):
        self.call_event("gotmore")

    async def _rcmd_mustlogin(self, args):
        self.call_event("mustlogin")

    async def _rcmd_v(self, args):
        self.call_event("v", args)

    async def _rcmd_badlogin(self, args):
        self.call_event("badlogin")

    async def _rcmd_badalias(self, args):
        self.call_event("badalias")

    async def _rcmd_aliasok(self, args):
        self.call_event("aliasok")

    async def _rcmd_chatango(self, args):
        self.call_event("chatango")

    async def _rcmd_limitexceeded(self, args):
        self.call_event("limitexceeded")

    async def _rcmd_verificationrequired(self, args):
        self.call_event("verificationrequired")

    async def _rcmd_verificationchanged(self, args):
        self.call_event("verificationchanged")

    async def _rcmd_versioningPU(self, args):
        self.call_event("versioningPU")

    async def _rcmd_badbansearchstring(self, args):
        self.call_event("badbansearchstring")

    async def _rcmd_bansearchresult(self, args):
        self.call_event("bansearchresult")

    async def _rcmd_allunblocked(self, args):
        self.call_event("allunblocked")
