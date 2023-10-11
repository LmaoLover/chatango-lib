#! /usr/bin/env python
# -*- coding: utf-8 -*-
import chatango
import asyncio
import time
import typing


class Config:
    user_name = ""
    passwd = ""
    rooms = ["asynclibraryinpython"]
    pm = False


class MyBot(chatango.Client):
    async def on_connect(self, room: typing.Union[chatango.Room, chatango.PM]):
        print("[info] Connected to {}".format(repr(room)))

    async def on_disconnect(self, room):
        print("[info] Disconnected from {}".format(repr(room)))

    async def on_message(self, message):
        print(
            time.strftime("%b/%d-%H:%M:%S", time.localtime(message.time)),
            message.room.name,
            message.user.showname,
            ascii(message.body)[1:-1],
        )


"""
Specials thanks to LmaoLover, TheClonerx
"""

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = MyBot(Config.user_name, Config.passwd, Config.rooms, pm=Config.pm)

    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        print("[KeyboardInterrupt] Killed bot.")
    finally:
        loop.stop()
        loop.close()
