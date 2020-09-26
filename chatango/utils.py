"""
Utility module
"""
import asyncio
import random, mimetypes
import typing
import html
import re, string, time
import asyncio, aiohttp
import urllib

class Task:
    ALIVE = False
    _INSTANCES = set()
    _LOCK = asyncio.Lock()
    _THREAD = None

    async def _heartbeat():
        if Task.ALIVE:
                # Only one call at a time for this method is allowed
            return
        Task.ALIVE = True
        while Task.ALIVE:
            await asyncio.sleep(0.01)
            await Task._tick()

    @staticmethod
    async def _tick():
        now = time.time()
        for task in list(Task._INSTANCES):
            try:
                if task.target <= now:
                    await task.func(*task.args, **task.kw)
                    if task.isInterval:
                        task.target = task.timeout + now
                    else:
                        task.cancel()
            except Exception as e:
                print("Task error {}: {}".format(task.func, e))
                task.cancel()
        if not Task._INSTANCES:
        
            Task.ALIVE = False
    
    def __init__(self, timeout, func = None, interval = False, *args, **kw):
        """
        Inicia una tarea nueva
        @param mgr: El dueño de esta tarea y el que la mantiene con vida
        """
        self.mgr = None
        self.func = func
        self.timeout = timeout
        self.target = time.time() + timeout
        self.isInterval = interval
        self.args = args
        self.kw = kw
        Task._INSTANCES.add(self)
        if not Task.ALIVE:
            Task._THREAD = asyncio.create_task(Task._heartbeat())
        
    def cancel(self):
        """Cancel task"""
        if self in Task._INSTANCES:
            Task._INSTANCES.remove(self)
                
    def __repr__(self):
        interval = "Interval" if self.isInterval else "Timeout"
        return f"<Task {interval}: {self.timeout} >"


async def get_token(user_name, passwd):
    chatango, token = ["http://chatango.com/login", "auth.chatango.com"], None
    payload = {
        "user_id": str(user_name).lower(),
        "password": str(passwd),
        "storecookie": "on",
        "checkerrors": "yes"}
    async with aiohttp.ClientSession() as session:
        async with session.post(chatango[0], data=payload) as resp:
            if chatango[1] in resp.cookies:
                token = str(resp.cookies[chatango[1]]).split("=")[1].split(";")[0]
    return token

def multipart(data, files, boundary=None):
    lineas = []
    def escape_quote(s):
        return s.replace('"', '\\"')
    if boundary == None:
        boundary = ''.join(
            random.choice(string.digits + string.ascii_letters) for x in range(30))
    for nombre, valor in data.items():
        lineas.extend(('--%s' % boundary,
                        'Content-Disposition: form-data; name="%s"' % nombre,
                        '', str(valor)))
    for nombre, valor in files.items():
        filename = valor['filename']
        if 'mimetype' in valor:
            mimetype = valor['mimetype']
        else:
            mimetype = mimetypes.guess_type(filename)[
                            0] or 'application/octet-stream'
        lineas.extend(('--%s' % boundary,
                        'Content-Disposition: form-data; name="%s"; '
                        'filename="%s"' % (
                            escape_quote(nombre), escape_quote(filename)),
                        'Content-Type: %s' % mimetype, '', valor['content']))
    lineas.extend(('--%s--' % boundary, '',))
    body = '\r\n'.join(lineas)
    headers = {
        'Content-Type':   'multipart/form-data; boundary=%s' % boundary,
        'Content-Length': str(len(body))
        }
    return body, headers
    
async def sessionget(session, url):
    async with session.get(url) as resp:
        assert resp.status == 200
        try:
            resp = await resp.text()
            return resp
        except:
            return None

async def make_requests(urls):
    r = {}
    async with aiohttp.ClientSession() as session:
        for x in urls:
            task = asyncio.create_task(sessionget(session, x[1]))
            r[x[0]] = task
        await asyncio.gather(*r.values())
    return r

def gen_uid() -> str:
    """
    Generate an uid
    """
    return str(random.randrange(10 ** 15, 10 ** 16))


def _clean_message(msg: str, pm: bool = False) -> [str, str, str]:  # TODO
    n = re.search("<n(.*?)/>", msg)
    tag = pm and 'g' or 'f'
    f = re.search("<" + tag + "(.*?)>", msg)
    msg = re.sub("<" + tag + ".*?>" + '|"<i s=sm://(.*)"', "", msg)

    # wink = '<i s="sm://wink" w="14.52" h="14.52"/>'

    if n:
        n = n.group(1)
    if f:
        f = f.group(1)
    msg = re.sub("<n.*?/>", "", msg)
    msg = _strip_html(msg)
    msg = html.unescape(msg).replace('\r', '\n')
    return msg, n or '', f or ''

def _account_selector(room):
    if room.client._using_accounts != None:
        accs = [x for x in room.client._using_accounts if x[0].lower() == room.user.name]
        data = [accs[0][0], accs[0][1]]
    else:
        data = [room.client._default_user_name, room.client._default_password]
    return data

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
    return ''.join(random.choice(string.ascii_uppercase) for i in range(4)).lower()

def get_anon_name(tssid: str, puid: str) -> str:
    puid = str(puid)
    tssid = str(tssid)
    result = []
    if len(puid) > 8:
        puid = puid[:8]
    if len(tssid) != 4:
        tssid = "3452"
    n = puid[-4:]
    for i in range(0, len(n)):
        number1 = int(n[i:i + 1])
        number2 = int(tssid[i:i + 1])
        result.append(str(number1+number2)[-1:])
    name = "".join(result)
    return 'anon' + name

def _parseFont(f: str, pm=False) -> (str, str, str):
    if pm:
        regex = r'x(\d{1,2})?s([a-fA-F0-9]{6}|[a-fA-F0-9]{3})="|\'(.*?)"|\''
    else:
        regex = r'x(\d{1,2})?([a-fA-F0-9]{6}|[a-fA-F0-9]{3})="(.*?)"'
    match = re.search(regex, f)
    if not match:
        return None, None, None
    return match.groups()

class Styles:
    def __init__(self):
        self._name_color = str("000000")
        self._font_color = str("000000")
        self._font_size = 11
        self._font_face = 1
        self._use_background = 0

        self._blend_name = None
        self._bgstyle = {
            'align': '', 'bgc': '', 
            'bgalp': '', 'hasrec': '0', 
            'ialp': '', 'isvid': '0', 
            'tile': '0', 'useimg': '0'
            }
        self._profile = dict(
            about = dict(age='', last_change='', 
                    gender='?',location='', d='', body=''),
            full=dict())

    def __dir__(self):
        return [x for x in
                set(list(self.__dict__.keys()) + list(dir(type(self)))) if
                x[0] != '_']

    def __repr__(self):
        return f"nc:{self.name_color} |bg:{self.use_background} |{self.default}"

    @property
    def aboutme(self):
        return self._profile["about"]

    @property
    def fullhtml(self):
        return html.escape(urllib.parse.unquote(self._profile["full"] or '')).replace('\r\n', '\n')

    @property
    def fullmini(self):
        return html.escape(urllib.parse.unquote(self._profile["about"]["body"] or '')).replace('\r\n', '\n')

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
        
