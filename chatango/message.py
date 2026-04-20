import re
import time
import enum
from typing import Optional

from .utils import get_anon_name, public_attributes
from .user import User
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
        self.channel: Optional[Channel] = None

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
        import html

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
        self.puid = str()
        self.ip = str()
        self.unid = str()
        self.flags = 0
        self.mentions = list()


async def _process(room, args):
    """Process message"""
    _time = float(args[0]) - room._correctiontime
    name, tname, puid, unid, msgid, ip, flags = args[1:8]
    body = ":".join(args[9:])
    msg = RoomMessage()
    msg.room = room
    msg.time = float(_time)
    msg.puid = str(puid)
    msg.id = msgid
    msg.unid = unid
    msg.ip = ip
    msg.raw = body
    msg.body = msg._clean_body_text
    isanon = False
    if not name:
        isanon = True
        if not tname:
            n_match = re.search(r"<n(\d{4})/?\s*>", msg.raw)
            n = n_match.group(1) if n_match else ""
            name = get_anon_name(n, puid)
        else:
            name = tname
    msg.user = User(name, ip=ip, isanon=isanon)
    msg.flags = MessageFlags(int(flags))
    msg.mentions = mentions(msg.body, room)
    msg.channel = Channel(msg.room, msg.user)
    ispremium = MessageFlags.PREMIUM in msg.flags
    if msg.user.ispremium != ispremium:
        evt = (
            msg.user._ispremium != None
            and ispremium != None
            and _time > time.time() - 5
        )
        msg.user._ispremium = ispremium
        if evt:
            room.call_event("premium_change", msg.user, ispremium)
    return msg


async def _process_pm(room, args):
    name = args[0] or args[1]
    if not name:
        name = args[2]
    user = User(name)
    mtime = float(args[3]) - room._correctiontime
    rawmsg = ":".join(args[5:])
    msg = PMMessage()
    msg.room = room
    msg.user = user
    msg.time = mtime
    msg.raw = rawmsg
    msg.body = msg._clean_body_text
    msg.channel = Channel(msg.room, msg.user)
    return msg


def message_cut(message, lenth):
    return [message[x : x + lenth] for x in range(0, len(message), lenth)]


def mentions(body, room):
    t = []
    for match in re.findall("(\s)?@([a-zA-Z0-9]{1,20})(\s)?", body):
        for participant in room.userlist:
            if participant.name.lower() == match[1].lower():
                if participant not in t:
                    t.append(participant)
    return t


class Channel:
    def __init__(self, room, user):
        self.is_pm = True if room.name == "<PM>" else False
        self.user = user
        self.room = room

    def __dir__(self):
        return public_attributes(self)

    async def send_message(self, message, use_html=False):
        messages = message_cut(message, self.room._maxlen)
        for message in messages:
            if self.is_pm:
                await self.room.send_message(self.user.name, message, use_html=use_html)
            else:
                await self.room.send_message(message, use_html=use_html)

    async def send_pm(self, message):
        self.is_pm = True
        await self.send_message(message)


# def format_videos(user, pmmessage): pass #TODO TESTING
#     msg = pmmessage
#     tag = 'i'
#     r = []
#     for word in msg.split(' '):
#         if msg.strip() != "":
#             regx = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?(?P<id>[A-Za-z0-9\-=_]{11})') #"<" + tag + "(.*?)>", msg)
#             match = regx.match(word)
#             w = "<g x{0._fontSize}s{0._fontColor}=\"{0._fontFace}\">".format(user)
#             if match:
#                 seek = match.group('id')
#                 word = f"<i s=\"vid','//yt','{seek}\" w=\"126\" h=\"93\"/>{w}"
#                 r.append(word)
#             else:
#                 if not r:
#                     r.append(w+word)
#                 else:
#                     r.append(word)
#             count = len([x for x in r if x == w])
#             print(count)

#     print(r)
#     return " ".join(r)
