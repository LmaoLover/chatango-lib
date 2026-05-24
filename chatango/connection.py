import asyncio
import aiohttp
import logging
import socket
from typing import Optional
from .handler import CommandHandler
from .utils import get_aiohttp_session

logger = logging.getLogger(__name__)


class WebsocketConnection(CommandHandler):
    def __init__(self):
        super().__init__()
        self._reset()

    def _reset(self):
        self._connected = False
        self._connection: Optional[aiohttp.ClientWebSocketResponse] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    @property
    def connected(self):
        return (
            self._connected
            and self._connection is not None
            and not self._connection.closed
        )

    async def _connect(self, url: str):
        try:
            self._connection = await get_aiohttp_session().ws_connect(
                url, origin="http://st.chatango.com"
            )
            self._connected = True
            self._recv_task = asyncio.create_task(self._do_recv())
            self._ping_task = asyncio.create_task(self._do_ping())
            logger.info(f"WebSocket connected to {url}")

        except (ValueError, TypeError, aiohttp.InvalidURL) as e:
            await self._disconnect()
            logger.critical(f"Invalid configuration for {url}: {e}")
            raise

        except (aiohttp.ClientConnectorSSLError, aiohttp.ClientSSLError) as e:
            await self._disconnect()
            logger.critical(f"SSL configuration error for {url}: {e}")
            raise

        except aiohttp.WSServerHandshakeError as e:
            await self._disconnect()
            error_str = str(e).lower()
            if (
                "404" in error_str and "websocket" in error_str
            ) or "upgrade required" in error_str:
                logger.error(f"WebSocket configuration error for {url}: {e}")
                raise
            else:
                logger.warning(f"WebSocket handshake failed for {url}: {e}")
                raise ConnectionError from e

        except aiohttp.ClientResponseError as e:
            await self._disconnect()
            if e.status == 404 and "websocket" in str(e).lower():
                logger.error(f"WebSocket endpoint not found for {url}: {e.message}")
                raise
            else:
                logger.warning(f"HTTP error {e.status} for {url}: {e.message}")
                raise ConnectionError from e

        except socket.gaierror as e:
            await self._disconnect()
            # DNS errors - retry most of them since DNS can be flaky
            host = url.split("//")[-1].split(":")[0]
            if (
                e.errno == socket.EAI_NONAME
                and not host.replace(".", "").replace("-", "").isalnum()
            ):
                logger.error(f"Invalid hostname {host}: {e}")
                raise
            else:
                logger.warning(f"DNS resolution failed for {url}: {e}")
                raise ConnectionError from e

        except (
            ConnectionResetError,
            ConnectionRefusedError,
            ConnectionAbortedError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
            aiohttp.ClientPayloadError,
        ) as e:
            await self._disconnect()
            logger.warning(f"Temporary connection failure for {url}: {e}")
            raise ConnectionError from e

        except aiohttp.ClientConnectorError as e:
            await self._disconnect()
            error_str = str(e).lower()
            if any(
                k in error_str
                for k in [
                    "name or service not known",
                    "nodename nor servname provided",
                    "no address associated with hostname",
                ]
            ):
                logger.error(f"Invalid hostname for {url}: {e}")
                raise
            else:
                logger.warning(f"Network connectivity issue for {url}: {e}")
                raise ConnectionError from e

        except aiohttp.ClientError as e:
            await self._disconnect()
            logger.warning(f"Client error for {url}: {e}")
            raise ConnectionError from e

        except Exception as e:
            await self._disconnect()
            logger.warning(f"Unexpected error connecting to {url}: {e}")
            raise ConnectionError from e

    async def _disconnect(self):
        self._connected = False
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass
        self._cancel_all_pending_futures()
        self._reset()
        logger.info("WebSocket disconnected")

    async def _send_command(self, command: str, terminator: str = "\r\n\0"):
        try:
            await self._connection.send_str(command + terminator)
        except Exception as e:
            logger.error(f'Message send failed "{command}": {e}')

    async def send_command(self, *args, **kwargs):
        if not self.connected:
            logger.error(f'Message send failed "{args[0]}": Not connected')
            raise ConnectionError("Cannot send message, websocket not connected")
        return await super().send_command(*args, **kwargs)

    async def _do_ping(self):
        """
        Ping the socket every minute to keep alive
        """
        try:
            while self.connected:
                await asyncio.sleep(90)
                if self.connected:
                    await self._send_command("\r\n", terminator="\x00")
        except asyncio.CancelledError:
            pass

    async def _do_recv(self):
        """
        Receives data from the websocket. When this task finishes, it signals
        that the connection is effectively dead.
        """
        try:
            while self.connected:
                try:
                    message = await asyncio.wait_for(
                        self._connection.receive(), timeout=180
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Websocket receive timeout, connection lost")
                    break
                except Exception as e:
                    logger.error(f"Websocket receive exception: {e}")
                    break

                if message.type == aiohttp.WSMsgType.TEXT:
                    if message.data:
                        # Chatango often sends multiple commands in one packet,
                        # delimited by null bytes or newlines.
                        raw_data = message.data
                        # Standard Chatango delimiter is \x00, sometimes \r\n\x00
                        cmds = raw_data.split("\x00")
                        for cmd in cmds:
                            clean_cmd = cmd.strip("\r\n")
                            if clean_cmd:
                                await self._receive_command(clean_cmd)
                elif message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
                else:
                    logger.error(f"Unexpected aiohttp.WSMsgType: {message.type}")
        except asyncio.CancelledError:
            pass
