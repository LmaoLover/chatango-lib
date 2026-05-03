import re
import time
import enum
import html
from typing import Optional

from .utils import public_attributes
from .user import User, UserManager
from .resources import Styles


class MessageFlags(enum.IntFlag):
    PREMIUM = 1 << 2
    BG_ON = 1 << 3
    MEDIA_ON = 1 << 4
    CENSORED = 1 << 5
    SHOW_MOD_ICON = 1 << 6
    SHOW_STAFF_ICON = 1 << 7
    DEFAULT_ICON = 1 << 6
    CHANNEL_RED = 1 << 8
    CHANNEL_ORANGE = 1 << 9
    CHANNEL_GREEN = 1 << 10
    CHANNEL_CYAN = 1 << 11
    CHANNEL_BLUE = 1 << 12
    CHANNEL_PURPLE = 1 << 13
    CHANNEL_PINK = 1 << 14
    CHANNEL_MOD = 1 << 15


Fonts = {
    "0": "Arial",
    "1": "Comic",
    "2": "Georgia",
    "3": "Handwriting",
    "4": "Impact",
    "5": "Palatino",
    "6": "Papyrus",
    "7": "Times",
    "8": "Typewriter",
}


class Message:
    def __init__(self):
        self.user: Optional[User] = None
        self.room = None
        self.time = 0.0
        self.body = str()
        self.raw = str()
        self._styles = None

    @property
    def styles(self):
        """Lazily creates a Styles object for this specific message instance."""
        if self._styles is None:
            is_pm = isinstance(self, PMMessage)
            self._styles = Styles.parse(self.raw, is_pm=is_pm)
        return self._styles

    def clear_styles(self):
        """Resets the style data to defaults."""
        self._styles = None

    @property
    def _clean_body_text(self):
        """Strips Chatango tags from the raw message to get clean body text."""
        is_pm = isinstance(self, PMMessage)
        tag = "g" if is_pm else "f"
        # Strip <n.../>, <f...>, <g...>, <b>, <i>, <u>, and closing tags
        text = re.sub(
            r"<(n|/?b|/?i|/?u|/?" + tag + r")[^>]*>", "", self.raw, flags=re.IGNORECASE
        )
        # Convert <br/> to newline
        text = re.sub(r"<br[^>]*>", "\n", text, flags=re.IGNORECASE)
        return html.unescape(text).replace("\r", "\n").strip()

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return f'<Message {self.room} {self.user} "{self.body}">'


class PMMessage(Message):
    def __init__(self):
        super().__init__()
        self.msgoff = False
        self.flags = str(0)


class RoomMessage(Message):
    def __init__(self):
        super().__init__()
        self.id = None
        self.short_cookie = str()
        self.ip = str()
        self.encoded_cookie = str()
        self.flags = 0


async def _process(room, args):
    """Process message"""
    _time = float(args[0]) - room.session.correction_time
    name, tname, aid, encoded_cookie, msgid, ip, flags = args[1:8]
    body = ":".join(args[9:])
    msg = RoomMessage()
    msg.room = room
    msg.time = float(_time)
    msg.short_cookie = str(aid)
    msg.id = msgid
    msg.encoded_cookie = encoded_cookie
    msg.ip = ip
    msg.raw = body
    msg.body = msg._clean_body_text

    if name:
        # Registered User
        msg.user = UserManager.get_user(name=name)
    else:
        # Anonymous or Temporary User
        n_match = re.search(r"<n(\d{4})/?\s*>", msg.raw)
        n = n_match.group(1) if n_match else "3452"
        msg.user = UserManager.get_user(name=tname, aid=aid, display_id=n)

    msg.flags = MessageFlags(int(flags))
    ispremium = MessageFlags.PREMIUM in msg.flags

    if msg.user._ispremium != ispremium:
        # Only call event if we knew the status before and it's not a historical message
        if msg.user._ispremium is not None and _time > time.time() - 5:
            room.call_event("premium_change", msg.user, ispremium)
        msg.user.ispremium = ispremium
    return msg


async def _process_pm(room, args):
    name = args[0] or args[1]
    if not name:
        name = args[2]
    user = UserManager.get_user(name=name)
    mtime = float(args[3]) - room.session.correction_time
    rawmsg = ":".join(args[5:])
    msg = PMMessage()
    msg.room = room
    msg.user = user
    msg.time = mtime
    msg.raw = rawmsg
    msg.body = msg._clean_body_text
    return msg


def message_cut(message, lenth):
    return [message[x : x + lenth] for x in range(0, len(message), lenth)]
