import time
import re
import json
import datetime
import xml.etree.ElementTree as ET
import urllib.parse
import asyncio
import logging
import socket
import aiohttp
from dataclasses import dataclass
from typing import Dict, Any, Optional, Type, TypeVar, List, Protocol, runtime_checkable

from .utils import get_aiohttp_session

T = TypeVar("T")
logger = logging.getLogger(__name__)


@runtime_checkable
class Fetchable(Protocol):
    """Protocol for resources that can be fetched via a class method."""

    @classmethod
    async def fetch(cls: Type[T], handle: str) -> T:
        ...


async def _get_data(url: str) -> Optional[str]:
    """Unified HTTP GET handler with robust exception and binary handling."""
    try:
        async with get_aiohttp_session().get(url) as resp:
            if resp.status == 200:
                # chatango sometimes returns a small image instead of XML for msgbg.xml
                content_type = resp.headers.get("Content-Type", "").lower()
                if "image" in content_type:
                    logger.debug(f"Received image instead of text from {url}")
                    return None
                try:
                    return await resp.text()
                except (UnicodeDecodeError, aiohttp.ClientPayloadError) as e:
                    logger.debug(f"Failed to decode text from {url}: {e}")
                    return None
            else:
                logger.warning(f"HTTP error {resp.status} fetching {url}")

    except (aiohttp.ClientResponseError, aiohttp.ClientConnectorError) as e:
        logger.warning(f"Aiohttp error fetching {url}: {e}")
    except (socket.gaierror, ConnectionResetError, asyncio.TimeoutError) as e:
        logger.warning(f"Network error fetching {url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
    return None


async def _post_data(url: str, data: Dict[str, str]) -> Optional[str]:
    """Unified HTTP POST handler with robust exception handling."""
    try:
        async with get_aiohttp_session().post(url, data=data) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.warning(f"HTTP error {resp.status} posting to {url}")

    except (aiohttp.ClientResponseError, aiohttp.ClientConnectorError) as e:
        logger.warning(f"Aiohttp error posting to {url}: {e}")
    except (socket.gaierror, ConnectionResetError, asyncio.TimeoutError) as e:
        logger.warning(f"Network error posting to {url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error posting to {url}: {e}")
    return None


async def fetch_resources(handle: str, resource_types: List[Type[Fetchable]]) -> List[Any]:
    """Fetches multiple resources in parallel for a given handle."""
    if not handle:
        return []

    tasks = [res_type.fetch(handle) for res_type in resource_types]
    return list(await asyncio.gather(*tasks))


class PathProvider:
    """Utility to generate canonical Chatango resource paths."""

    @staticmethod
    def get_user_path(handle: str) -> str:
        """Resource path logic for a given user or room"""
        handle = handle.lower()
        if not handle:
            return ""

        special_chars = "-_"
        first_initial = handle[0]
        second_initial = handle[1] if len(handle) > 1 else handle[0]

        if first_initial in special_chars or second_initial in special_chars:
            return f"/sp/sp{handle}"

        return f"/{first_initial}/{second_initial}/{handle}"

    @classmethod
    def get_resource_url(
        cls, handle: str, resource: str, domain: str = "http://ust.chatango.com"
    ) -> str:
        """Constructs a full URL for a specific resource."""
        path = cls.get_user_path(handle)
        if not path:
            return ""

        # Group profiles live in /groupinfo, everything else in /profileimg
        root = "/groupinfo" if resource == "gprofile.xml" else "/profileimg"
        return f"{domain}{root}{path}/{resource}"


@dataclass
class MessageBackground:
    """Models message background properties from msgbg.xml."""

    align: str = "tl"
    bg_alpha: int = 100
    bg_color: str = "ffffff"
    use_image: bool = False
    image_alpha: int = 100
    tile: bool = False
    is_video: bool = False
    last_update: float = 0.0

    @classmethod
    def from_xml(cls, data: str) -> "MessageBackground":
        """Parses msgbg.xml content, robust against malformed or non-XML text."""
        obj = cls()
        try:
            if not data or "<bgi" not in data.lower():
                return obj

            data = re.sub(r"<\?xml.*?\?>", "", data).strip()
            if not data:
                return obj
            root = ET.fromstring(data)
            if root.tag != "bgi":
                root = root.find(".//bgi")

            if root is not None:
                obj.align = root.get("align", obj.align)
                obj.bg_alpha = int(root.get("bgalp", obj.bg_alpha))
                obj.bg_color = root.get("bgc", obj.bg_color)
                obj.use_image = root.get("useimg") == "1"
                obj.image_alpha = int(root.get("ialp", obj.image_alpha))
                obj.tile = root.get("tile") == "1"
                obj.is_video = root.get("isvid") == "1"
                obj.last_update = time.time()
        except (ET.ParseError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse msgbg.xml: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error parsing msgbg.xml: {e}")
        return obj

    def to_dict(self) -> Dict[str, str]:
        """Converts background properties to a dictionary for the /updatemsgbg API."""
        return {
            "align": self.align,
            "bgalp": str(self.bg_alpha),
            "bgc": self.bg_color,
            "useimg": "1" if self.use_image else "0",
            "ialp": str(self.image_alpha),
            "tile": "1" if self.tile else "0",
            "isvid": "1" if self.is_video else "0",
        }

    @classmethod
    async def fetch(cls, handle: str) -> "MessageBackground":
        url = PathProvider.get_resource_url(handle, "msgbg.xml")
        data = await _get_data(f"{url}?cb={time.time()}")
        return cls.from_xml(data) if data else cls()

    @classmethod
    async def save(cls, handle: str, password: str, obj: "MessageBackground") -> bool:
        url = "https://chatango.com/updatemsgbg"
        data = obj.to_dict()
        data.update(
            {
                "lo": handle,
                "p": password,
                "hasrec": str(int(obj.last_update)),
            }
        )
        return await _post_data(url, data) is not None


@dataclass
class Styles:
    """Models message styles."""

    name_color: str = "000000"
    text_color: str = "000000"
    font_size: int = 11
    font_face: int = 0
    bold: bool = False
    italics: bool = False
    underline: bool = False
    use_background: bool = False
    styles_on: bool = True

    @staticmethod
    def compress_hex(hex_str: str) -> str:
        """Reduces 6-digit hex to 3-digit if pairs match (e.g., AABBCC -> ABC)."""
        if not hex_str:
            return ""
        h = hex_str.lstrip("#")
        if len(h) == 6:
            if h[0] == h[1] and h[2] == h[3] and h[4] == h[5]:
                return h[0] + h[2] + h[4]
        return h

    @classmethod
    def parse(cls, raw: str, is_pm: bool = False) -> "Styles":
        """Parses a Styles object from a raw websocket message string."""
        styles = cls()

        # Extract name color
        n_match = re.search(r"<n([0-9a-fA-F]+)/?>", raw)
        if n_match:
            val = n_match.group(1)
            if len(val) == 4 and val.isdigit():
                pass
            elif len(val) == 1:
                styles.name_color = val * 3
            elif len(val) in (3, 6):
                styles.name_color = val

        # Extract font tags
        tag = "g" if is_pm else "f"
        f_match = re.search(r"<" + tag + r"(.*?)>", raw)
        if f_match:
            from .utils import _parseFont

            sz, clr, fc = _parseFont(f_match.group(1), pm=is_pm)
            styles.font_size = int(sz) if sz else 11
            styles.text_color = clr or "000000"
            styles.font_face = int(fc) if fc else 0

        # Extract text style flags
        raw_lower = raw.lower()
        styles.bold = "<b>" in raw_lower
        styles.italics = "<i>" in raw_lower
        styles.underline = "<u>" in raw_lower

        return styles

    @classmethod
    def from_json(cls, data: str) -> "Styles":
        """Parses msgstyles.json content."""
        obj = cls()
        try:
            raw = json.loads(data)
            obj.name_color = raw.get("nameColor", obj.name_color)
            obj.text_color = raw.get("textColor", obj.text_color)
            obj.font_size = int(raw.get("fontSize", obj.font_size))
            obj.font_face = int(raw.get("fontFamily", obj.font_face))
            obj.bold = str(raw.get("bold", "")) == "1" or raw.get("bold") is True
            obj.italics = (
                str(raw.get("italics", "")) == "1" or raw.get("italics") is True
            )
            obj.underline = (
                str(raw.get("underline", "")) == "1" or raw.get("underline") is True
            )
            obj.use_background = str(raw.get("usebackground", "0")) == "1"
            obj.styles_on = str(raw.get("stylesOn", "1")) == "1"
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse msgstyles.json: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error parsing msgstyles.json: {e}")
        return obj

    def get_font_tag(self, is_pm: bool = False) -> str:
        """Generates the opening <f> or <g> tag for messages using optimized protocol rules."""
        tag = "g" if is_pm else "f"
        f_prefix = f"<{tag} x"
        has_style = False

        if self.font_size != 11:
            f_prefix += f"{self.font_size:02d}"
            has_style = True

        if self.text_color != "000000":
            f_prefix += f"s{self.compress_hex(self.text_color)}" if is_pm else self.compress_hex(self.text_color)
            has_style = True

        f_prefix += '="'
        if self.font_face != 0:
            f_prefix += str(self.font_face)
            has_style = True
        f_prefix += '">'

        return f_prefix if has_style else ""

    def format_message(self, text: str, is_pm: bool = False, is_anon: bool = False) -> str:
        """Wraps text in the appropriate tags based on current styles and user type."""
        if is_anon:
            return text

        inner = text
        if self.bold:
            inner = f"<b>{inner}</b>"
        if self.italics:
            inner = f"<i>{inner}</i>"
        if self.underline:
            inner = f"<u>{inner}</u>"

        if not self.styles_on:
            return inner

        f_tag = self.get_font_tag(is_pm)
        if f_tag:
            return f"{f_tag}{inner}"
        return inner

    def get_name_tag(self, is_anon: bool = False) -> str:
        """Generates the <n{color}/> tag for username color."""
        if is_anon or not self.name_color:
            return ""
        return f"<n{self.compress_hex(self.name_color)}/>"

    def to_dict(self) -> Dict[str, str]:
        """Converts styles to a dictionary compatible with the Chatango /updatemsgstyles API."""
        return {
            "nameColor": self.name_color,
            "textColor": self.text_color,
            "fontSize": str(self.font_size),
            "fontFamily": str(self.font_face),
            "bold": "1" if self.bold else "0",
            "italics": "1" if self.italics else "0",
            "underline": "1" if self.underline else "0",
            "usebackground": "1" if self.use_background else "0",
            "stylesOn": "1" if self.styles_on else "0",
        }

    @classmethod
    async def fetch(cls, handle: str) -> "Styles":
        url = PathProvider.get_resource_url(handle, "msgstyles.json")
        data = await _get_data(f"{url}?cb={time.time()}")
        return cls.from_json(data) if data else cls()

    @classmethod
    async def save(cls, handle: str, password: str, obj: "Styles") -> bool:
        url = "https://chatango.com/updatemsgstyles"
        data = obj.to_dict()
        data.update(
            {
                "lo": handle,
                "p": password,
                "hasrec": str(int(time.time())),
            }
        )
        return await _post_data(url, data) is not None

    @property
    def font_color(self) -> str:
        return self.text_color

    @font_color.setter
    def font_color(self, value: str):
        self.text_color = value


@dataclass
class UserProfile:
    """Models user profile data from mod1.xml."""

    gender: str = "?"
    location: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country_code: str = ""
    is_gps: bool = False
    birthdate: str = ""
    body_html: str = ""
    premium_expiry: Optional[int] = None
    last_update: float = 0.0

    @property
    def age(self) -> str:
        if not self.birthdate:
            return ""
        try:
            year = int(self.birthdate.split("-")[0])
            return str(abs(datetime.datetime.now().year - year))
        except (ValueError, IndexError):
            return ""

    @classmethod
    def from_mod1(cls, data: str) -> "UserProfile":
        """Parses mod1.xml content."""
        obj = cls()
        try:
            data = re.sub(r"<\?xml.*?\?>", "", data).strip()
            if not data:
                return obj
            root = ET.fromstring(f"<root>{data}</root>")
            mod = root.find("mod")
            if mod is None:
                return obj

            s_tag = mod.find("s")
            if s_tag is not None:
                obj.gender = s_tag.text or "?"

            l_tag = mod.find("l")
            if l_tag is not None:
                obj.location = l_tag.text or ""
                obj.latitude = float(l_tag.get("lat", 0)) or None
                obj.longitude = float(l_tag.get("lon", 0)) or None
                obj.country_code = l_tag.get("c", "")
                obj.is_gps = l_tag.get("g") == "1"

            b_tag = mod.find("b")
            if b_tag is not None:
                obj.birthdate = b_tag.text or ""

            body_tag = mod.find("body")
            if body_tag is not None and body_tag.text:
                obj.body_html = urllib.parse.unquote(body_tag.text)

            d_tag = mod.find("d")
            if d_tag is not None and d_tag.text:
                try:
                    obj.premium_expiry = int(d_tag.text)
                except ValueError:
                    pass

            obj.last_update = time.time()
        except (ET.ParseError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse mod1.xml: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error parsing mod1.xml: {e}")
        return obj

    def to_dict(self) -> Dict[str, str]:
        """Converts profile properties to a dictionary compatible with the /updateprofile API."""
        return {
            "age": self.age,
            "gender": self.gender,
            "location": self.location,
            "line": self.body_html,
        }

    def update_from_query_string(self, data: str):
        """Parses the ampersand-separated response from /updateprofile."""
        try:
            params = urllib.parse.parse_qs(data)
            self.gender = params.get("sex", [self.gender])[0]
            self.location = params.get("loc", [self.location])[0]
            if "eline" in params:
                self.body_html = params["eline"][0]

            self.last_update = time.time()
        except Exception as e:
            logger.debug(f"Failed to parse query string in update_from_query_string: {e}")

    @classmethod
    async def fetch(cls, handle: str) -> "UserProfile":
        url = PathProvider.get_resource_url(handle, "mod1.xml")
        data = await _get_data(f"{url}?cb={time.time()}")
        return cls.from_mod1(data) if data else cls()

    @classmethod
    async def save(cls, handle: str, password: str, obj: "UserProfile") -> bool:
        url = "https://chatango.com/updateprofile"

        # 1. First POST to fetch fields (as seen in JS load())
        base_data = {
            "u": handle,
            "p": password,
            "auth": "pwd",
            "arch": "h5",
            "src": "group",
        }

        try:
            resp_text = await _post_data(url, base_data)
            if resp_text:
                obj.update_from_query_string(resp_text)
        except Exception as e:
            logger.debug(f"Error during initial profile POST: {e}")

        # 2. Second POST to update (as seen in JS update())
        update_data = base_data.copy()
        update_data.update(obj.to_dict())
        update_data["action"] = "update"

        return await _post_data(url, update_data) is not None


@dataclass
class RoomProfile:
    """Models room profile data from gprofile.xml."""

    group_title: str = ""
    group_body_html: str = ""
    last_update: float = 0.0

    @classmethod
    def from_xml(cls, data: str) -> "RoomProfile":
        """Parses gprofile.xml content looking for <gp> tags."""
        obj = cls()
        try:
            data = re.sub(r"<\?xml.*?\?>", "", data).strip()
            if not data:
                return obj

            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                root = ET.fromstring(f"<root>{data}</root>")

            gp = root if root.tag == "gp" else root.find(".//gp")
            if gp is not None:
                desc_tag = gp.find("desc")
                if desc_tag is not None and desc_tag.text:
                    obj.group_body_html = urllib.parse.unquote(desc_tag.text)

                title_tag = gp.find("title")
                if title_tag is not None and title_tag.text:
                    obj.group_title = urllib.parse.unquote(title_tag.text)

                obj.last_update = time.time()
        except (ET.ParseError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse gprofile.xml: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error parsing gprofile.xml: {e}")
        return obj

    def to_dict(self, handle: str) -> Dict[str, str]:
        """Converts profile properties to a dictionary for the /updategroupprofile API."""
        return {
            "erase": "0",
            "l": "1",
            "d": self.group_body_html,
            "n": self.group_title,
            "u": handle,
        }

    @classmethod
    async def fetch(cls, handle: str) -> "RoomProfile":
        url = PathProvider.get_resource_url(handle, "gprofile.xml")
        data = await _get_data(f"{url}?cb={time.time()}")
        return cls.from_xml(data) if data else cls()

    @classmethod
    async def save(cls, handle: str, password: str, obj: "RoomProfile") -> bool:
        url = "https://chatango.com/updategroupprofile"
        data = obj.to_dict(handle)
        data.update(
            {
                "lo": handle,
                "p": password,
            }
        )
        return await _post_data(url, data) is not None
