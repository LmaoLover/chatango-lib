import re
import enum
import html
from collections import OrderedDict
from typing import Any

from .utils import public_attributes
from .user import User, Session
from .resources import Styles


class Command:
    def __init__(self, raw: str):
        self.raw = raw
        self._parts = raw.split(":")

    def __repr__(self):
        return f"<Command {self.name} args={self.args}>"

    @property
    def name(self):
        return self._parts[0]

    @property
    def args(self):
        return self._parts[1:]

    @property
    def fields(self):
        return tuple(self._parts)


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
        self.time = 0.0
        self.body = str()
        self.raw = str()
        self._styles = None

    @property
    def user(self):
        raise NotImplementedError

    @property
    def room(self):
        raise NotImplementedError

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

    @classmethod
    def clean_body_text(cls, raw: str) -> str:
        """Strips Chatango tags from the raw message to get clean body text."""
        # Strip <n.../>, <f...>, <g...>, <b>, <i>, <u>, and closing tags
        text = re.sub(r"<(n|/?b|/?i|/?u|/?f|/?g)[^>]*>", "", raw, flags=re.IGNORECASE)
        # Convert <br/> to newline
        text = re.sub(r"<br[^>]*>", "\n", text, flags=re.IGNORECASE)
        return html.unescape(text).replace("\r", "\n").strip()

    def __dir__(self):
        return public_attributes(self)

    def __repr__(self):
        return f'<Message {self.room} {self.user} "{self.body}">'


class PMMessage(Message):
    def __init__(self, user, room):
        super().__init__()
        self._user = user
        self._room = room
        self.id = None
        self.msgoff = False
        self.flags = str(0)

    @property
    def user(self):
        return self._user

    @property
    def room(self):
        return self._room


class RoomMessage(Message):
    def __init__(self, session: Session, id):
        super().__init__()
        self.session = session
        self.id: str = id
        self.flags = 0
        self.deleted = False

    @property
    def user(self) -> User:
        return self.session.user

    @property
    def room(self):
        return self.session.room

    @property
    def ip(self):
        return self.session.ip

    @property
    def short_cookie(self):
        return self.session.short_cookie

    @property
    def encoded_cookie(self):
        return self.session.encoded_cookie

    def __repr__(self):
        return f'<RoomMessage {self.room.name} {self.user.name} {"deleted " if self.deleted else ""}"{self.body}">'


def message_cut(message, lenth):
    return [message[x : x + lenth] for x in range(0, len(message), lenth)]


class MessageHistory(OrderedDict):
    """Combined dict + deque for message storage based on OrderedDict.

    Supports bounded size like deque(maxlen). Iterates over values
    (messages) rather than keys. Supports negative integer indexing
    for access to newest/oldest items.

    When full, appending a new key evicts the oldest entry (left side).
    appendleft() discards the incoming message when full.
    """

    def __init__(self, *args, maxlen: int = 20000, **kwargs):
        self.maxlen = maxlen
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        """Set item, evicting oldest if at capacity and key is new."""
        if self.maxlen > 0 and key not in self and len(self) >= self.maxlen:
            self.popitem(last=False)
        super().__setitem__(key, value)

    def append(self, key: str, value: Any) -> None:
        """Add item to the right (newest). Evicts oldest if full."""
        self[key] = value

    def appendleft(self, key: str, value: Any) -> bool:
        """Add item to the left (oldest). Returns False if discarded."""
        if self.maxlen > 0 and len(self) >= self.maxlen and key not in self:
            return False
        super().__setitem__(key, value)
        self.move_to_end(key, last=False)
        return True

    def last(self) -> Any:
        """Return the most recent message."""
        it = reversed(self.values())
        return next(it, None)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self.values())

    def __reversed__(self):
        return reversed(self.values())

    def copy(self):
        new = MessageHistory(maxlen=self.maxlen)
        for key, value in self.items():
            new[key] = value
        return new

    def __copy__(self):
        return self.copy()

    def __or__(self, other):
        new = self.copy()
        new.update(other)
        return new

    def __ror__(self, other):
        new = MessageHistory(maxlen=self.maxlen)
        new.update(other)
        new.update(self)
        return new
