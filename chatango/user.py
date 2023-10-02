import enum
import json, urllib
from typing import Any, Optional
import re
import time
import datetime
from collections import deque

from .utils import http_get, public_attributes


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
            self._name = name.lower()
            self._ip = None
            self._flags = 0
            self._history = deque(maxlen=5)
            self._isanon = False
            self._sids = dict()
            self._showname = name
            self._ispremium = None
            self._puid = str()
            self._client = None
            self._last_time = None
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
    def age(self):
        return self.styles._profile["about"]["age"]

    @property
    def last_change(self):
        return self.styles._profile["about"]["last_change"]

    @property
    def gender(self):
        return self.styles._profile["about"]["gender"]

    @property
    def location(self):
        return self.styles._profile["about"]["location"]

    @property
    def get_user_dir(self):
        if not self.isanon:
            return "/%s/%s/" % ("/".join((self.name * 2)[:2]), self.name)

    @property
    def fullpic(self):
        if not self.isanon:
            return f"{self._fp}{self.get_user_dir}full.jpg"
        return False

    @property
    def last_time(self):
        return self._last_time

    @property
    def msgbg(self):
        if not self.isanon:
            return f"{self._fp}{self.get_user_dir}msgbg.jpg"
        return False

    @property
    def styles(self):
        return self._styles

    @property
    def thumb(self):
        if not self.isanon:
            return f"{self._fp}{self.get_user_dir}thumb.jpg"
        return False

    @property
    def _fp(self):
        return "http://fp.chatango.com/profileimg"

    @property
    def _ust(self):
        return "http://ust.chatango.com/profileimg"

    @property
    def name(self):
        return self._name

    @property
    def puid(self):
        return self._puid

    @property
    def ispremium(self):
        return self._ispremium

    @property
    def showname(self):
        return self._showname

    @property
    def links(self):
        return {
            "msgstyles": f"{self._ust}{self.get_user_dir}msgstyles.json",
            "msgbg": f"{self._ust}{self.get_user_dir}msgbg.xml",
            "mod1": f"{self._ust}{self.get_user_dir}mod1.xml",
            "mod2": f"{self._ust}{self.get_user_dir}mod2.xml",
        }

    @property
    def isanon(self):
        return self._isanon

    def setName(self, val):
        self._showname = val
        self._name = val.lower()

    def del_profile(self):
        if self.styles.profile:
            del self._styles._profile
            self._styles._profile.update(dict(about={}, full={}))

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

    async def get_styles(self):
        position_dict = {
            "tl": "top left",
            "tr": "top right",
            "bl": "bottom left",
            "br": "bottom right",
        }
        if not self.isanon:
            msg_styles = await http_get(self.links["msgstyles"])
            msg_bg = await http_get(self.links["msgbg"])
            if msg_bg:
                bg = msg_bg.replace('<?xml version="1.0" ?>', "")
                bg_dict = dict(
                    url.replace('"', "").split("=")
                    for url in re.findall('(\w+=".*?")', bg)
                )
                self._styles._bgstyle.update(bg_dict)
                self._styles._bgstyle["align"] = position_dict.get(
                    self._styles._bgstyle["align"]
                )
            if msg_styles:
                try:
                    styles = json.loads(msg_styles)
                    self._styles._name_color = styles["nameColor"]
                    self._styles._font_face = int(styles["fontFamily"])
                    self._styles._font_size = int(styles["fontSize"])
                    self._styles._font_color = styles["textColor"]
                    self._styles._use_background = int(styles["usebackground"])
                except json.JSONDecodeError:
                    pass

    async def get_main_profile(self):
        if not self.isanon:
            items = await http_get(self.links["mod1"])
            if items is not None:
                about = items.replace('<?xml version="1.0" ?>', "")
                gender_start = about.find("<s>")
                gender_end = about.find("</s>", gender_start)
                gender = (
                    about[gender_start + 3 : gender_end] if gender_start != -1 else "?"
                )
                self._styles._profile["about"]["gender"] = gender

                location_start = about.find("<l")
                location_end = about.find("</l>", location_start)
                location = (
                    about[location_start + 2 : location_end]
                    if location_start != -1
                    else ""
                )
                self._styles._profile["about"]["location"] = location

                last_change_start = about.find("<b>")
                last_change_end = about.find("</b>", last_change_start)
                last_change = (
                    about[last_change_start + 3 : last_change_end]
                    if last_change_start != -1
                    else ""
                )
                self._styles._profile["about"]["last_change"] = last_change

                if last_change:
                    age = abs(
                        datetime.datetime.now().year - int(last_change.split("-")[0])
                    )
                    self._styles._profile["about"]["age"] = age
                body_start = about.find("<body>")
                body_end = about.find("</body>", body_start)
                body = about[body_start + 6 : body_end] if body_start != -1 else ""
                self._styles._profile["about"].update(
                    {"body": urllib.parse.unquote(body)}
                )

            try:
                full_prof = await http_get(self.links["mod2"])
                if full_prof is not None and str(full_prof)[:5] == "<?xml":
                    full_prof_start = full_prof.find("<body")
                    full_prof_end = full_prof.find("</body>", full_prof_start)
                    full_prof_body = (
                        full_prof[full_prof_start + len("<body") : full_prof_end]
                        if full_prof_start != -1
                        else ""
                    )
                    self._styles._profile["full"] = full_prof_body
            except (AttributeError, KeyError):
                pass


class Styles:
    def __init__(
        self,
        name_color=None,
        font_color=None,
        font_face=None,
        font_size=None,
        use_background=None,
    ):
        self._name_color = name_color if name_color else str("000000")
        self._font_color = font_color if font_color else str("000000")
        self._font_size = font_size if font_size else 11
        self._font_face = font_face if font_face else 0
        self._use_background = int(use_background) if use_background else 0

        self._blend_name = None
        self._bgstyle = {
            "align": "",
            "bgc": "",
            "bgalp": "",
            "hasrec": "0",
            "ialp": "",
            "isvid": "0",
            "tile": "0",
            "useimg": "0",
        }
        self._profile = dict(
            about=dict(age="", last_change="", gender="?", location="", d="", body=""),
            full=dict(),
        )

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return f"nc:{self.name_color} |bg:{self.use_background} |{self.default}"

    @property
    def aboutme(self):
        return self._profile["about"]

    @property
    def fullhtml(self):
        o = html.escape(urllib.parse.unquote(self._profile["full"] or "")).replace(
            "\r\n", "\n"
        )
        if o:
            return o
        else:
            return None

    @property
    def fullmini(self):
        o = html.escape(
            urllib.parse.unquote(self._profile["about"]["body"] or "")
        ).replace("\r\n", "\n")
        if o:
            return o
        else:
            return None

    @property
    def bgstyle(self):
        return self._bgstyle

    @property
    def use_background(self):
        return self._use_background

    @property
    def default(self):
        size = str(self.font_size)
        face = str(self.font_face)
        return f"<f x{size}{self.font_color}='{face}'>"

    @property
    def name_color(self):
        return self._name_color

    @property
    def font_color(self):
        return self._font_color

    @property
    def font_size(self):
        return self._font_size

    @property
    def font_face(self):
        return self._font_face


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
