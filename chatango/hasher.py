"""
Chatango md5-lite hashing algorithm

This is used only to lookup a server based on
the group name.  Some groups use this method
instead of the weights based method.  Check
first using the hash lookup then fallback to
weights.
"""

import ctypes


FFFFVAL = ctypes.c_int32(0xFFFFFFFF).value


def left(a, b):
    m = ctypes.c_int32(a)
    m.value <<= b
    return m.value


def rightr(a, b):
    m = ctypes.c_int32(a)
    m.value >>= b
    return m.value


def right(val, n):
    max_shift = 31
    n = n % max_shift

    mask = (1 << 32) - 1
    val = val & mask

    if n > 0:
        val >>= n

    return val


def xor(a, b):
    a = ctypes.c_int32(a).value
    b = ctypes.c_int32(b).value
    return a ^ b


def andd(a, b):
    a = ctypes.c_int32(a).value
    b = ctypes.c_int32(b).value
    return a & b


def orr(a, b):
    a = ctypes.c_int32(a).value
    b = ctypes.c_int32(b).value
    return a | b


def nott(val):
    mask = (1 << 32) - 1
    return ~(val & mask)


class Hasher:
    def __init__(self):
        self.block_size = 64
        self.state = [1732584193, 4023233417, 2562383102, 271733878]
        self.message_array = [0] * self.block_size
        self.message_length = self.buffer_length = 0

    def compress(self, msg, i=None):
        if not i:
            i = 0

        tmp = [0] * 16

        if isinstance(msg, str):
            for f in range(16):
                tmp[f] = (
                    ord(msg[i])
                    | (ord(msg[i + 1]) << 8)
                    | (ord(msg[i + 2]) << 16)
                    | (ord(msg[i + 3]) << 24)
                )
                i += 4
        else:
            for f in range(16):
                tmp[f] = (
                    msg[i] | (msg[i + 1] << 8) | (msg[i + 2] << 16) | (msg[i + 3] << 24)
                )
                i += 4

        a, b, c, d, t = self.state[0], self.state[1], self.state[2], self.state[3], 0
        t = andd(a + xor(d, andd(b, xor(c, d))) + tmp[0] + 3614090360, FFFFVAL)
        a = b + ((left(t, 7)) & FFFFVAL | right(t, 25))
        t = andd(d + xor(c, andd(a, xor(b, c))) + tmp[1] + 3905402710, FFFFVAL)
        d = a + (left(t, 12) & FFFFVAL | t >> 20)
        t = andd(c + xor(b, andd(d, xor(a, b))) + tmp[2] + 606105819, FFFFVAL)
        c = d + ((left(t, 17)) & FFFFVAL | right(t, 15))
        t = andd(b + xor(a, andd(c, xor(d, a))) + tmp[3] + 3250441966, FFFFVAL)
        b = c + ((left(t, 22)) & FFFFVAL | right(t, 10))
        t = andd(a + xor(d, andd(b, xor(c, d))) + tmp[4] + 4118548399, FFFFVAL)
        a = b + ((left(t, 7)) & FFFFVAL | right(t, 25))
        t = andd(d + xor(c, andd(a, xor(b, c))) + tmp[5] + 1200080426, FFFFVAL)
        d = a + ((left(t, 12)) & FFFFVAL | right(t, 20))
        t = andd(c + xor(b, andd(d, xor(a, b))) + tmp[6] + 2821735955, FFFFVAL)
        c = d + ((left(t, 17)) & FFFFVAL | right(t, 15))
        t = andd(b + xor(a, andd(c, xor(d, a))) + tmp[7] + 4249261313, FFFFVAL)
        b = c + ((left(t, 22)) & FFFFVAL | right(t, 10))
        t = andd(a + xor(d, andd(b, xor(c, d))) + tmp[8] + 1770035416, FFFFVAL)
        a = b + ((left(t, 7)) & FFFFVAL | right(t, 25))
        t = andd(d + xor(c, andd(a, xor(b, c))) + tmp[9] + 2336552879, FFFFVAL)
        d = a + ((left(t, 12)) & FFFFVAL | right(t, 20))
        t = andd(c + xor(b, andd(d, xor(a, b))) + tmp[10] + 4294925233, FFFFVAL)
        c = d + ((left(t, 17)) & FFFFVAL | right(t, 15))
        t = andd(b + xor(a, andd(c, xor(d, a))) + tmp[11] + 2304563134, FFFFVAL)
        b = c + ((left(t, 22)) & FFFFVAL | right(t, 10))
        t = andd(a + xor(d, andd(b, xor(c, d))) + tmp[12] + 1804603682, FFFFVAL)
        a = b + ((left(t, 7)) & FFFFVAL | right(t, 25))
        t = andd(d + xor(c, andd(a, xor(b, c))) + tmp[13] + 4254626195, FFFFVAL)
        d = a + ((left(t, 12)) & FFFFVAL | right(t, 20))
        t = andd(c + xor(b, andd(d, xor(a, b))) + tmp[14] + 2792965006, FFFFVAL)
        c = d + ((left(t, 17)) & FFFFVAL | right(t, 15))
        t = andd(b + xor(a, andd(c, xor(d, a))) + tmp[15] + 1236535329, FFFFVAL)
        b = c + ((left(t, 22)) & FFFFVAL | right(t, 10))
        t = andd(a + xor(c, andd(d, xor(b, c))) + tmp[1] + 4129170786, FFFFVAL)
        a = b + ((left(t, 5)) & FFFFVAL | right(t, 27))
        t = andd(d + xor(b, andd(c, xor(a, b))) + tmp[6] + 3225465664, FFFFVAL)
        d = a + ((left(t, 9)) & FFFFVAL | right(t, 23))
        t = andd(c + xor(a, andd(b, xor(d, a))) + tmp[11] + 643717713, FFFFVAL)
        c = d + ((left(t, 14)) & FFFFVAL | right(t, 18))
        t = andd(b + xor(d, andd(a, xor(c, d))) + tmp[0] + 3921069994, FFFFVAL)
        b = c + ((left(t, 20)) & FFFFVAL | right(t, 12))
        t = andd(a + xor(c, andd(d, xor(b, c))) + tmp[5] + 3593408605, FFFFVAL)
        a = b + ((left(t, 5)) & FFFFVAL | right(t, 27))
        t = andd(d + xor(b, andd(c, xor(a, b))) + tmp[10] + 38016083, FFFFVAL)
        d = a + ((left(t, 9)) & FFFFVAL | right(t, 23))
        t = andd(c + xor(a, andd(b, xor(d, a))) + tmp[15] + 3634488961, FFFFVAL)
        c = d + ((left(t, 14)) & FFFFVAL | right(t, 18))
        t = andd(b + xor(d, andd(a, xor(c, d))) + tmp[4] + 3889429448, FFFFVAL)
        b = c + ((left(t, 20)) & FFFFVAL | right(t, 12))
        t = andd(a + xor(c, andd(d, xor(b, c))) + tmp[9] + 568446438, FFFFVAL)
        a = b + ((left(t, 5)) & FFFFVAL | right(t, 27))
        t = andd(d + xor(b, andd(c, xor(a, b))) + tmp[14] + 3275163606, FFFFVAL)
        d = a + ((left(t, 9)) & FFFFVAL | right(t, 23))
        t = andd(c + xor(a, andd(b, xor(d, a))) + tmp[3] + 4107603335, FFFFVAL)
        c = d + ((left(t, 14)) & FFFFVAL | right(t, 18))
        t = andd(b + xor(d, andd(a, xor(c, d))) + tmp[8] + 1163531501, FFFFVAL)
        b = c + ((left(t, 20)) & FFFFVAL | right(t, 12))
        t = andd(a + xor(c, andd(d, xor(b, c))) + tmp[13] + 2850285829, FFFFVAL)
        a = b + ((left(t, 5)) & FFFFVAL | right(t, 27))
        t = andd(d + xor(b, andd(c, xor(a, b))) + tmp[2] + 4243563512, FFFFVAL)
        d = a + ((left(t, 9)) & FFFFVAL | right(t, 23))
        t = andd(c + xor(a, andd(b, xor(d, a))) + tmp[7] + 1735328473, FFFFVAL)
        c = d + ((left(t, 14)) & FFFFVAL | right(t, 18))
        t = andd(b + xor(d, andd(a, xor(c, d))) + tmp[12] + 2368359562, FFFFVAL)
        b = c + ((left(t, 20)) & FFFFVAL | right(t, 12))
        t = andd(a + xor(xor(b, c), d) + tmp[5] + 4294588738, FFFFVAL)
        a = b + ((left(t, 4)) & FFFFVAL | right(t, 28))
        t = andd(d + xor(xor(a, b), c) + tmp[8] + 2272392833, FFFFVAL)
        d = a + ((left(t, 11)) & FFFFVAL | right(t, 21))
        t = andd(c + xor(xor(d, a), b) + tmp[11] + 1839030562, FFFFVAL)
        c = d + ((left(t, 16)) & FFFFVAL | right(t, 16))
        t = andd(b + xor(xor(c, d), a) + tmp[14] + 4259657740, FFFFVAL)
        b = c + ((left(t, 23)) & FFFFVAL | right(t, 9))
        t = andd(a + xor(xor(b, c), d) + tmp[1] + 2763975236, FFFFVAL)
        a = b + ((left(t, 4)) & FFFFVAL | right(t, 28))
        t = andd(d + xor(xor(a, b), c) + tmp[4] + 1272893353, FFFFVAL)
        d = a + ((left(t, 11)) & FFFFVAL | right(t, 21))
        t = andd(c + xor(xor(a, b), d) + tmp[7] + 4139469664, FFFFVAL)
        c = d + ((left(t, 16)) & FFFFVAL | right(t, 16))
        t = andd(b + xor(xor(c, d), a) + tmp[10] + 3200236656, FFFFVAL)
        b = c + ((left(t, 23)) & FFFFVAL | right(t, 9))
        t = andd(a + xor(xor(b, c), d) + tmp[13] + 681279174, FFFFVAL)
        a = b + ((left(t, 4)) & FFFFVAL | right(t, 28))
        t = andd(d + xor(xor(a, b), c) + tmp[0] + 3936430074, FFFFVAL)
        d = a + ((left(t, 11)) & FFFFVAL | right(t, 21))
        t = andd(c + xor(xor(a, b), d) + tmp[3] + 3572445317, FFFFVAL)
        c = d + ((left(t, 16)) & FFFFVAL | right(t, 16))
        t = andd(b + xor(xor(c, d), a) + tmp[6] + 76029189, FFFFVAL)
        b = c + ((left(t, 23)) & FFFFVAL | right(t, 9))
        t = andd(a + xor(xor(b, c), d) + tmp[9] + 3654602809, FFFFVAL)
        a = b + ((left(t, 4)) & FFFFVAL | right(t, 28))
        t = andd(d + xor(xor(a, b), c) + tmp[12] + 3873151461, FFFFVAL)
        d = a + ((left(t, 11)) & FFFFVAL | right(t, 21))
        t = andd(c + xor(xor(a, b), d) + tmp[15] + 530742520, FFFFVAL)
        c = d + ((left(t, 16)) & FFFFVAL | right(t, 16))
        t = andd(b + xor(xor(c, d), a) + tmp[2] + 3299628645, FFFFVAL)
        b = c + ((left(t, 23)) & FFFFVAL | right(t, 9))
        t = andd(a + xor(c, orr(b, nott(d))) + tmp[0] + 4096336452, FFFFVAL)
        a = b + ((left(t, 6)) & FFFFVAL | right(t, 26))
        t = andd(d + xor(b, orr(a, nott(c))) + tmp[7] + 1126891415, FFFFVAL)
        d = a + ((left(t, 10)) & FFFFVAL | right(t, 22))
        t = andd(c + xor(a, orr(d, nott(b))) + tmp[14] + 2878612391, FFFFVAL)
        c = d + ((left(t, 15)) & FFFFVAL | right(t, 17))
        t = andd(b + xor(d, orr(c, nott(a))) + tmp[5] + 4237533241, FFFFVAL)
        b = c + ((left(t, 21)) & FFFFVAL | right(t, 11))
        t = andd(a + xor(c, orr(b, nott(d))) + tmp[12] + 1700485571, FFFFVAL)
        a = b + ((left(t, 6)) & FFFFVAL | right(t, 26))
        t = andd(d + xor(b, orr(a, nott(c))) + tmp[3] + 2399980690, FFFFVAL)
        d = a + ((left(t, 10)) & FFFFVAL | right(t, 22))
        t = andd(c + xor(a, orr(d, nott(b))) + tmp[10] + 4293915773, FFFFVAL)
        c = d + ((left(t, 15)) & FFFFVAL | right(t, 17))
        t = andd(b + xor(d, orr(c, nott(a))) + tmp[1] + 2240044497, FFFFVAL)
        b = c + ((left(t, 21)) & FFFFVAL | right(t, 11))
        t = andd(a + xor(c, orr(b, nott(d))) + tmp[8] + 1873313359, FFFFVAL)
        a = b + ((left(t, 6)) & FFFFVAL | right(t, 26))
        t = andd(d + xor(b, orr(a, nott(c))) + tmp[15] + 4264355552, FFFFVAL)
        d = a + ((left(t, 10)) & FFFFVAL | right(t, 22))
        t = andd(c + xor(a, orr(d, nott(b))) + tmp[6] + 2734768916, FFFFVAL)
        c = d + ((left(t, 15)) & FFFFVAL | right(t, 17))
        t = andd(b + xor(d, orr(c, nott(a))) + tmp[13] + 1309151649, FFFFVAL)
        b = c + ((left(t, 21)) & FFFFVAL | right(t, 11))
        t = andd(a + xor(c, orr(b, nott(d))) + tmp[4] + 4149444226, FFFFVAL)
        a = b + ((left(t, 6)) & FFFFVAL | right(t, 26))
        t = andd(d + xor(b, orr(a, nott(c))) + tmp[11] + 3174756917, FFFFVAL)
        d = a + ((left(t, 10)) & FFFFVAL | right(t, 22))
        t = andd(c + xor(a, orr(d, nott(b))) + tmp[2] + 718787259, FFFFVAL)
        c = d + ((left(t, 15)) & FFFFVAL | right(t, 17))
        t = andd(b + xor(d, orr(c, nott(a))) + tmp[9] + 3951481745, FFFFVAL)

        self.state[0] = self.state[0] + andd(a, FFFFVAL)
        self.state[1] = self.state[1] + andd(
            c + orr(andd(left(t, 21), FFFFVAL), right(t, 11)), FFFFVAL
        )
        self.state[2] = self.state[2] + andd(c, FFFFVAL)
        self.state[3] = self.state[3] + andd(d, FFFFVAL)

    def update(self, message, length=None):
        if not length:
            length = len(message)

        c = length - self.block_size
        msg_array = self.message_array
        l = self.buffer_length
        m = 0

        while m < length:
            if l == 0:
                while m <= c:
                    self.compress(message, m)
                    m += self.block_size

            if isinstance(message, str):
                while m < length:
                    msg_array[l] = ord(message[m])
                    l += 1
                    m += 1

                    if l == self.block_size:
                        self.compress(msg_array)
                        l = 0
                        break
            else:
                while m < length:
                    msg_array[l] = message[m]
                    l += 1
                    m += 1

                    if l == self.block_size:
                        self.compress(msg_array)
                        l = 0
                        break

        self.buffer_length = l
        self.message_length += length
        return self

    def finalize(self):
        size = (
            self.block_size if self.buffer_length < 56 else 2 * self.block_size
        ) - self.buffer_length

        a = [0] * size

        a[0] = 128
        for i in range(1, size - 8):
            a[i] = 0

        c = 8 * self.message_length
        for i in range(size - 8, size):
            a[i] = int(c) & 255
            c /= 256

        self.update(a)

        a = [0] * 16
        c = 0
        for i in range(4):
            for j in range(0, 32, 8):
                a[c] = (self.state[i] >> j) & 255
                c += 1

        return a

    def hash(self, input):
        message = self.update(input).finalize()
        return "".join(["{:02x}".format(num) for num in message])
