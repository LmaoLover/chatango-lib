import asyncio
import random
import mimetypes
import html
import re
import string
import asyncio
import aiohttp
import urllib
import logging
from typing import Tuple

from .hasher import Hasher

# fmt: off
specials = {
    'mitvcanal': 56, 'animeultimacom': 34, 'cricket365live': 21,
    'pokemonepisodeorg': 22, 'animelinkz': 20, 'sport24lt': 56,
    'narutowire': 10, 'watchanimeonn': 22, 'cricvid-hitcric-': 51,
    'narutochatt': 70, 'leeplarp': 27, 'stream2watch3': 56, 'ttvsports': 56,
    'ver-anime': 8, 'vipstand': 21, 'eafangames': 56, 'soccerjumbo': 21,
    'myfoxdfw': 67, 'kiiiikiii': 21, 'de-livechat': 5, 'rgsmotrisport': 51,
    'dbzepisodeorg': 10, 'watch-dragonball': 8, 'peliculas-flv': 69,
    'tvanimefreak': 54, 'tvtvanimefreak': 54
}

# order matters
tsweights = [
    [5, 75], [6, 75], [7, 75], [8, 75], [16, 75],
    [17, 75], [18, 75], [9, 95], [11, 95], [12, 95],
    [13, 95], [14, 95], [15, 95], [19, 110], [23, 110],
    [24, 110], [25, 110], [26, 110], [28, 104], [29, 104],
    [30, 104], [31, 104], [32, 104], [33, 104], [35, 101],
    [36, 101], [37, 101], [38, 101], [39, 101], [40, 101],
    [41, 101], [42, 101], [43, 101], [44, 101], [45, 101],
    [46, 101], [47, 101], [48, 101], [49, 101], [50, 101],
    [52, 110], [53, 110], [55, 110], [57, 110],
    [58, 110], [59, 110], [60, 110], [61, 110],
    [62, 110], [63, 110], [64, 110], [65, 110],
    [66, 110], [68, 95], [71, 116], [72, 116],
    [73, 116], [74, 116], [75, 116], [76, 116],
    [77, 116], [78, 116], [79, 116], [80, 116],
    [81, 116], [82, 116], [83, 116], [84, 116]
]
# fmt: on

tshashes = {
    "91e8b91e78e378682a3947488fd9316c": 90,
    "eca6a363f0993d54a68ee8bf728a5022": 10,
    "3cb1dd65d3d9942201d148be315e1353": 51,
    "598d57ebdbc4be60d2c3bf7065646fb1": 86,
    "125c2cad391f1c2af6b8dbeabbc40711": 20,
    "5ab6580bb5752e550c7d2ad0e7107d4f": 22,
    "bd1a9d7accc0fa8e277b81f1940b8af1": 10,
    "35f8fb4a3a52203d74ba411d2907ec8e": 27,
    "7a11f77f7c5d651461cc1b56cb3295f8": 27,
    "d56fa7039b1da8ea297fa57c70dd028a": 20,
    "1345c176737e37abf3a0f0b9c8d901ef": 90,
    "c661ada6edbb1821396e0201e0e47725": 85,
    "e5d6942071bb235e9fc4a5ce2dfdcef2": 86,
    "140dde0776002a40c3a13888ac7bdb66": 87,
    "3ca3259ee67dd2aca09cb769c52e2463": 91,
    "8ae453bf7ebecb862af15dfff69ce321": 34,
    "71fef04a40db4cd5a681736b8dff18ae": 27,
    "d53fcb14ac6097b6cf3dd9d8364f23d9": 90,
    "5e2882b7386fbb3d59667aaa34452d9a": 85,
    "4cc2baaf65ed713e81edadd38de27af0": 91,
    "ca4a0f825f254ac3e302e81848d977a2": 51,
    "7a63f4590acc91c2619b87b541c3fbca": 22,
    "471ddb2a802b14faa52e16ff80cc8231": 88,
    "e3b4b482cb1a23475c3d71b7131cf5bf": 69,
    "bcfc527d6c18322a1cd4daffd3022ce7": 10,
    "636fdb8e231dd084b92a83ac43e5c2aa": 27,
    "357125725bb8dd47034cba16d52b809d": 88,
    "15f46d7975c1c9916daaceb8747240fc": 69,
    "856c72b367c65b0a8b313d3d46d1bb79": 67,
    "310dc0a3acd14f7c8047de4226956473": 22,
    "c823c6c1178cd0bda18fb9c92af92a56": 90,
    "fadf6f0ce3fea6c05db69c7894d1c593": 89,
    "940401e30ccba30826f8390e99706e68": 34,
    "7ef3a503fcc80806be880c22f2a3eb69": 20,
    "3ec844c0ff298c660ca06324c7f05cf0": 70,
    "5e7cc3e1526a20190cc8b3299ff011c2": 92,
    "106e74ab50d5e68259f82331c87499ca": 56,
    "b6b88e4bcb579d4f535a54e2382d7644": 88,
    "a31f7787f27b227fbb7bfedf87391415": 51,
    "9dd2b303a9b243a95e7faa6ef9e1fc9f": 54,
    "5d27aab7be0ce5be3014b511cc3e92f1": 92,
}


def get_server(group):
    """
    Get the server host for a certain room.

    @type group: str
    @param group: room name

    @rtype: str
    @return: the server's hostname
    """
    sn = specials.get(group)
    if not sn:
        hash = Hasher().hash(group)
        sn = tshashes.get(hash)
    if not sn:
        group = group.replace("_", "q")
        group = group.replace("-", "q")
        fnv = int(group[:5], 36)
        lnv = group[6:9]
        if lnv:
            lnv = int(lnv, 36)
            lnv = max(lnv, 1000)
        else:
            lnv = 1000
        num = (fnv % lnv) / lnv
        maxnum = sum(y for x, y in tsweights)
        cumfreq = 0
        sn = 0
        for x, y in tsweights:
            cumfreq += float(y) / maxnum
            if num <= cumfreq:
                sn = x
                break
    return f"s{sn}.chatango.com"


def public_attributes(obj):
    return [
        x for x in set(list(obj.__dict__.keys()) + list(dir(type(obj)))) if x[0] != "_"
    ]


async def on_request_exception(session, context, params):
    logging.getLogger("aiohttp.client").debug(f"on request exception: <{params}>")


def trace():
    trace_config = aiohttp.TraceConfig()
    trace_config.on_request_exception.append(on_request_exception)
    return trace_config


_aiohttp_session = None


def get_aiohttp_session():
    global _aiohttp_session
    if _aiohttp_session is None:
        _aiohttp_session = aiohttp.ClientSession(trace_configs=[trace()])
    return _aiohttp_session


async def get_token(user_name, passwd):
    chatango, token = ["http://chatango.com/login", "auth.chatango.com"], None
    payload = {
        "user_id": str(user_name).lower(),
        "password": str(passwd),
        "storecookie": "on",
        "checkerrors": "yes",
    }
    # Use fresh session to retrieve auth cookies
    async with aiohttp.ClientSession().post(chatango[0], data=payload) as resp:
        if chatango[1] in resp.cookies:
            token = str(resp.cookies[chatango[1]]).split("=")[1].split(";")[0]
    return token


def multipart(data, files, boundary=None):
    lineas = []

    def escape_quote(s):
        return s.replace('"', '\\"')

    if boundary == None:
        boundary = "".join(
            random.choice(string.digits + string.ascii_letters) for x in range(30)
        )
    for nombre, valor in data.items():
        lineas.extend(
            (
                "--%s" % boundary,
                'Content-Disposition: form-data; name="%s"' % nombre,
                "",
                str(valor),
            )
        )
    for nombre, valor in files.items():
        filename = valor["filename"]
        if "mimetype" in valor:
            mimetype = valor["mimetype"]
        else:
            mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        lineas.extend(
            (
                "--%s" % boundary,
                'Content-Disposition: form-data; name="%s"; '
                'filename="%s"' % (escape_quote(nombre), escape_quote(filename)),
                "Content-Type: %s" % mimetype,
                "",
                valor["content"],
            )
        )
    lineas.extend(
        (
            "--%s--" % boundary,
            "",
        )
    )
    body = "\r\n".join(lineas)
    headers = {
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Content-Length": str(len(body)),
    }
    return body, headers

    # async def upload_image(self, path, return_url=False):
    #     if self.user.isanon:
    #         return None
    #     with open(path, mode="rb") as f:
    #         files = {
    #             "filedata": {"filename": path, "content": f.read().decode("latin-1")}
    #         }
    #     data, headers = multipart(
    #         dict(u=self.client._default_user_name, p=self.client._default_password),
    #         files,
    #     )
    #     headers.update({"host": "chatango.com", "origin": "http://st.chatango.com"})
    #     async with get_aiohttp_session.post(
    #         "http://chatango.com/uploadimg",
    #         data=data.encode("latin-1"),
    #         headers=headers,
    #     ) as resp:
    #         response = await resp.text()
    #         if "success" in response:
    #             success = response.split(":", 1)[1]
    #     if success != None:
    #         if return_url:
    #             url = "http://ust.chatango.com/um/{}/{}/{}/img/t_{}.jpg"
    #             return url.format(
    #                 self.user.name[0], self.user.name[1], self.user.name, success
    #             )
    #         else:
    #             return f"img{success}"
    #     return None


async def sessionget(session: aiohttp.ClientSession, url: str):
    async with session.get(url) as resp:
        assert resp.status == 200
        try:
            resp = await resp.text()
            return resp
        except:
            return None


async def make_requests(urls):
    r = {}
    for x in urls:
        task = asyncio.create_task(sessionget(get_aiohttp_session(), x[1]))
        r[x[0]] = task
    await asyncio.gather(*r.values())
    return r


def gen_uid() -> str:
    """
    Generate an uid
    """
    return str(random.randrange(10**15, 10**16))


def _clean_message(msg: str, pm: bool = False) -> Tuple[str, str, str]:
    n = re.search("<n(.*?)/>", msg)
    tag = pm and "g" or "f"
    f = re.search("<" + tag + "(.*?)>", msg)
    msg = re.sub("<" + tag + ".*?>" + '|"<i s=sm://(.*)"', "", msg)
    if n:
        n = n.group(1)
    if f:
        f = f.group(1)
    msg = re.sub("<n.*?/>", "", msg)
    msg = _strip_html(msg)
    msg = html.unescape(msg).replace("\r", "\n")
    return msg, n or "", f or ""


def _strip_html(msg: str) -> str:
    li = msg.split("<")
    if len(li) == 1:
        return li[0]
    else:
        ret = list()
        for data in li:
            data = data.split(">", 1)
            if len(data) == 1:
                ret.append(data[0])
            elif len(data) == 2:
                if data[0].startswith("br"):
                    ret.append("\n")
                ret.append(data[1])
        return "".join(ret)


def _id_gen():
    return "".join(random.choice(string.ascii_uppercase) for i in range(4)).lower()


def get_anon_name(tssid: str, puid: str) -> str:
    puid = puid.zfill(8)[4:8]
    ts = str(tssid)
    if not ts or len(ts) < 4:
        ts = "3452"
    else:
        ts = ts.split(".")[0][-4:]
    __reg5 = ""
    __reg1 = 0
    while __reg1 < len(puid):
        __reg4 = int(puid[__reg1])
        __reg3 = int(ts[__reg1])
        __reg2 = str(__reg4 + __reg3)
        __reg5 += __reg2[-1:]
        __reg1 += 1
    return "anon" + __reg5.zfill(4)


def _fontFormat(text):
    # TODO check
    """Converts */_ into whattsap like formats"""
    formats = {"/": "I", "\*": "B", "_": "U"}
    for f in formats:
        f1, f2 = set(formats.keys()) - {f}
        # find = ' <?[BUI]?>?[{0}{1}]?{2}(.+?[\S]){2}'.format(f1, f2, f+'{1}')
        find = " <?[BUI]?>?[{0}{1}]?{2}(.+?[\S]?[{2}]?){2}[{0}{1}]?[\s]".format(
            f1, f2, f
        )
        for x in re.findall(find, " " + text + " "):
            original = f[-1] + x + f[-1]
            cambio = "<" + formats[f] + ">" + x + "</" + formats[f] + ">"
            text = text.replace(original, cambio)
    return text


def _parseFont(f: str, pm=False) -> Tuple[str, str, str]:
    """
    Lee el contendido de un etiqueta f y regresa
    tamaño color y fuente (en ese orden)
    @param f: El texto con la etiqueta f incrustada
    @return: Tamaño, Color, Fuente
    """
    if pm:
        regex = r'x(\d{1,2})?s([a-fA-F0-9]{6}|[a-fA-F0-9]{3})="|\'(.*?)"|\''
    else:
        regex = r'x(\d{1,2})?([a-fA-F0-9]{6}|[a-fA-F0-9]{3})="(.*?)"'
    match = re.search(regex, f)
    if not match:
        return "11", "000000", "0"
    else:
        return match.groups()


def _videoImagePMFormat(text):
    """Returns text with formatted video and image for PM sending"""
    for x in re.findall("(http[s]?://[^\s]+outube.com/watch\?v=([^\s]+))", text):
        original = x[0]
        cambio = '<i s="vid://yt:%s" w="126" h="96"/>' % x[1]
        text = text.replace(original, cambio)
    for x in re.findall("(http[s]?://[\S]+outu.be/([^\s]+))", text):
        original = x[0]
        cambio = '<i s="vid://yt:%s" w="126" h="96"/>' % x[1]
        text = text.replace(original, cambio)
    for x in re.findall("http[s]?://[\S]+?.jpg", text):
        text = text.replace(x, '<i s="%s" w="70.45" h="125"/>' % x)
    return text


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


def _convert_dict(src):
    r = {}
    m = src.split(" ")
    for w in m:
        k, v = w.split("=")
        r.update({k: v})
    return r
