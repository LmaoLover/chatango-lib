import time
import asyncio
from typing import Optional

from .utils import get_token, gen_uid, public_attributes
from .exceptions import AlreadyConnectedError
from .handler import CommandHandler, EventHandler, TaskHandler
from .user import User, Friend
from .message import _process_pm, message_cut


class Socket(CommandHandler):
    def __init__(self):
        self._reset()

    def _reset(self):
        self._connected = False
        self._first_command = True
        self._recv: Optional[asyncio.StreamReader] = None
        self._connection: Optional[asyncio.StreamWriter] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    @property
    def connected(self):
        return self._connected

    async def _connect(self, server: str, port: int):
        self._recv, self._connection = await asyncio.open_connection(server, port)
        self._connected = True
        self._recv_task = asyncio.create_task(self._do_recv())
        self._ping_task = asyncio.create_task(self._do_ping())

    async def _disconnect(self):
        if self._ping_task:
            self._ping_task.cancel()
        if self._connection:
            self._connection.close()
            await self._connection.wait_closed()
        self._reset()

    async def _send_command(self, command, terminator="\r\n\0"):
        if self._first_command:
            terminator = "\x00"
            self._first_command = False
        else:
            terminator = "\r\n\0"
        message = command + terminator
        if self._connection:
            self._connection.write(message.encode())
            await self._connection.drain()

    async def _do_ping(self):
        """
        Ping the socket every minute to keep alive
        """
        while True:
            await asyncio.sleep(90)
            if self.connected:
                await self._send_command("\r\n", terminator="\x00")

    async def _do_recv(self):
        """
        Receive and process data from the socket
        """
        while self._recv:
            data: bytes = await self._recv.read(2048)
            if self.connected and data:
                data_str: str = data.decode()
                if data_str != "\r\n\x00":  # pong
                    cmds = data_str.split("\r\n\x00")
                    for cmd in cmds:
                        await self._receive_command(cmd)
            else:
                break
            await asyncio.sleep(0.0001)
        await self._disconnect()


class PM(Socket, EventHandler):
    def __init__(self):
        super().__init__()
        self.server = "c1.chatango.com"
        self.port = 443
        self.user = None
        self.reconnect = False
        self.__token = None
        self._correctiontime = 0

        # misc
        self._uid = gen_uid()
        self._silent = 0
        self._maxlen = 11600
        self._friends = dict()
        self._blocked = list()
        self._premium = False
        self._history = list()

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return "<PM>"

    @property
    def name(self):
        return repr(self)

    @property
    def is_pm(self):
        return True

    @property
    def premium(self):
        return self._premium

    @property
    def history(self):
        return self._history

    @property
    def blocked(self):
        return self._blocked

    @property
    def friends(self):
        return list(self._friends.keys())

    async def connect(self, user_name: str, password: str):
        if self.connected:
            raise AlreadyConnectedError(self.name)
        await self._connect(self.server, self.port)
        await self._login(user_name, password)

    async def _login(self, user_name: str, password: str):
        if not self.__token:
            self.__token = await get_token(user_name, password)
        if self.__token:
            await self.send_command("tlogin", self.__token, "2", self._uid)
            self.user = User(user_name)

    async def connection_wait(self):
        if self._recv_task:
            await self._recv_task
        self.call_event("pm_disconnect")

    async def disconnect(self):
        self.reconnect = False
        await self._disconnect()

    async def listen(self, user_name: str, password: str, reconnect=False):
        self.reconnect = reconnect
        while True:
            await self.connect(user_name, password)
            await self.connection_wait()
            if not self.reconnect:
                break
            await asyncio.sleep(3)
        await self.complete_tasks()
        self.end_tasks()

    async def send_message(self, target, message: str, use_html: bool = False):
        if isinstance(target, User):
            target = target.name
        if self._silent > time.time():
            self.call_event("pm_silent", message)
        else:
            if len(message) > 0:
                message = message  # format_videos(self.user, message)
                nc, fs, fc, ff = (
                    f"<n{self.user.styles.name_color}/>",
                    f"{self.user.styles.font_size}",
                    f"{self.user.styles.font_color}",
                    f"{self.user.styles.font_face}",
                )
                for msg in message_cut(message, self._maxlen):
                    msg = f'{nc}<m v="1"><g xs0="0"><g x{fs}s{fc}="{ff}">{msg}</g></g></m>'
                    await self.send_command("msg", target.lower(), msg)

    async def block(self, user):  # TODO
        if isinstance(user, User):
            user = user.name
        if user not in self._blocked:
            await self.send_command("block", user, user, "S")
            self._blocked.append(User(user))
            self.call_event("pm_block", User(user))

    async def unblock(self, user):
        if isinstance(user, User):
            user = user.name
        if user in self._blocked:
            await self.send_command("unblock", user)
            self.call_event("pm_unblock", User(user))
            return True

    def get_friend(self, user):
        if isinstance(user, User):
            user = user.name
        if user.lower() in self.friends:
            return self._friends[user]
        return None

    def _add_to_history(self, args):
        if len(self.history) >= 10000:
            self._history = self._history[1:]
        self._history.append(args)

    async def enable_bg(self):
        await self.send_command("msgbg", "1")

    async def disable_bg(self):
        await self.send_command("msgbg", "0")

    async def addfriend(self, user_name):
        user = user_name
        friend = self.get_friend(user)
        if not friend:
            await self.send_command("wladd", user_name.lower())

    async def unfriend(self, user_name):
        user = user_name
        friend = self.get_friend(user)
        if friend:
            await self.send_command("wldelete", friend.name)

    async def _rcmd_seller_name(self, args):
        self.call_event("pm_connect")

    async def _rcmd_premium(self, args):
        if args and args[0] == "210":
            self._premium = True
        else:
            self._premium = False
        if self.premium:
            await self.enable_bg()

    async def _rcmd_time(self, args):
        self._connectiontime = float(args[0])
        self._correctiontime = float(self._connectiontime) - time.time()

    async def _rcmd_kickingoff(self, args):
        self.call_event("pm_kickingoff", args)
        self.__token = None
        await self._disconnect()

    async def _rcmd_DENIED(self, args):
        self.call_event("pm_denied", args)
        self.__token = None
        await self._disconnect()

    async def _rcmd_OK(self, args):
        if self.friends or self.blocked:
            self.friends.clear()
            self.blocked.clear()
        await self.send_command("getpremium")
        await self.send_command("wl")
        await self.send_command("getblock")

    async def _rcmd_toofast(self, args):
        self._silent = time.time() + 12  # seconds to wait
        self.call_event("pm_toofast")

    async def _rcmd_msglexceeded(self, args):
        self.call_event("pm_msglexceeded")

    async def _rcmd_msg(self, args):
        msg = await _process_pm(self, args)
        self._add_to_history(msg)
        self.call_event("pm_message", msg)

    async def _rcmd_msgoff(self, args):
        msg = await _process_pm(self, args)
        msg._offline = True
        self._add_to_history(msg)

    async def _rcmd_wlapp(self, args):
        pass

    async def _rcmd_wloffline(self, args):
        pass

    async def _rcmd_wlonline(self, args):
        pass

    async def _rcmd_wl(self, args):
        # Restart contact list
        self._friends.clear()
        # Iterate over each contact
        for i in range(len(args) // 4):
            name, last_on, is_on, idle = args[i * 4 : i * 4 + 4]
            user = User(name)
            friend = Friend(user, self)
            if last_on == "None":
                last_on = 0
            if is_on in ["off", "offline"]:
                friend._status = "offline"
            elif is_on in ["on", "online"]:
                friend._status = "online"
            elif is_on in ["app"]:
                friend._status = "app"
            friend._check_status(float(last_on), None, int(idle))
            self._friends[str(user.name)] = friend
            await self.send_command("track", user.name)

    async def _rcmd_track(self, args):
        friend = self._friends[args[0]] if args[0] in self.friends else None
        if friend:
            friend._idle = False
            if args[2] == "online":
                friend._last_active = time.time() - (int(args[1]) * 60)
            elif args[2] == "offline":
                friend._last_active = float(args[1])
            if args[1] in ["0"] and args[2] in ["app"]:
                friend._status = "app"
            else:
                friend._status = args[2]

    async def _rcmd_idleupdate(self, args):
        friend = self._friends[args[0]] if args[0] in self.friends else None
        if friend:
            friend._last_active = time.time()
            friend._idle = True if args[1] == "0" else False

    async def _rcmd_status(self, args):
        friend = self._friends[args[0]] if args[0] in self.friends else None
        if friend == None:
            return
        status = True if args[2] == "online" else False
        friend._check_status(float(args[1]), status, 0)
        self.call_event(f"pm_contact_{args[2]}", friend)

    async def _rcmd_block_list(self, args):
        self.call_event("pm_block_list")

    async def _rcmd_wladd(self, args):
        if args[1] == "invalid":
            return
        friend = self._friends[args[0]] if args[0] in self.friends else None
        if not friend:
            friend = Friend(User(args[0]), self)
            self._friends[args[0]] = friend
            self.call_event("pm_contact_addfriend", friend)
            await self.send_command("wl")
            await self.send_command("track", args[0].lower())

    async def _rcmd_wldelete(self, args):
        if args[1] == "deleted":
            friend = args[0]
            if friend in self._friends:
                del self._friends[friend]
                self.call_event("pm_contact_unfriend", args[0])
