"""Header-rewriting reverse proxy in front of DuckDB's UI server.

The `ui` extension binds only to IPv6 loopback and rejects requests that do
not look like they came from an unproxied `localhost:4213` client. This proxy
listens on the network and rewrites each request to look like that client.
It replaces the HAProxy that used to run beside the server.
"""

import asyncio
import logging
import re
import signal

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_LOCAL_HOST = "localhost:4213"
_REWRITTEN = {
    "host": f"Host: {_LOCAL_HOST}",
    "origin": f"Origin: http://{_LOCAL_HOST}",
    "referer": f"Referer: http://{_LOCAL_HOST}/",
}
_DROPPED = {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-port", "x-forwarded-host"}
_REQUEST_LINE = re.compile(rb"^[A-Z]{3,10} [^\s]+ HTTP/1\.[01]$")
_HEADER_NAME = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HEAD_END = b"\r\n\r\n"
_HEAD_TIMEOUT = 30.0
_HALF_CLOSE_GRACE = 30.0


class DuckDBUISettings(BaseSettings):
    """Ports of the proxy and of DuckDB's UI server."""

    model_config = SettingsConfigDict(env_prefix="DUCKDB_UI_")

    listen_port: int = 8080
    upstream_host: str = "::1"
    upstream_port: int = 4213
    max_head_bytes: int = 65536


class BadRequestError(ValueError):
    """The request head is malformed or too large."""


def rewrite_head(head: bytes) -> bytes:
    """Rewrite a request head so DuckDB's UI server accepts it.

    Sets `Host`, `Origin` and `Referer` to the localhost values and removes
    the `X-Forwarded-*` headers. Forces `Connection: close` unless the request
    upgrades, so no later request skips the rewrite.

    Args:
        head: The request line and headers, without the final blank line.

    Returns:
        The rewritten head, with a trailing blank line.

    Raises:
        BadRequestError: If a line is malformed.
    """
    request_line, *lines = head.split(b"\r\n")
    if not _REQUEST_LINE.match(request_line):
        msg = "malformed request line"
        raise BadRequestError(msg)
    out = [request_line.decode("ascii")]
    upgrade = False
    for line in lines:
        name, sep, value = line.partition(b":")
        if not sep or not _HEADER_NAME.match(name):
            msg = "malformed header line"
            raise BadRequestError(msg)
        key = name.decode("ascii").lower()
        if key in _REWRITTEN or key in _DROPPED or key == "connection":
            continue
        upgrade = upgrade or key == "upgrade"
        out.append(f"{name.decode('ascii')}:{value.decode('latin-1')}")
    out.extend(_REWRITTEN.values())
    if not upgrade:
        out.append("Connection: close")
    else:
        out.append("Connection: Upgrade")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


async def _read_head(reader: asyncio.StreamReader, limit: int) -> tuple[bytes, bytes]:
    """Read the request head and return it with any bytes read past it."""
    buffer = b""
    while _HEAD_END not in buffer:
        if len(buffer) > limit:
            msg = "request head too large"
            raise BadRequestError(msg)
        chunk = await reader.read(4096)
        if not chunk:
            msg = "connection closed before the request head ended"
            raise BadRequestError(msg)
        buffer += chunk
    head, _, rest = buffer.partition(_HEAD_END)
    return head, rest


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, half_close: bool = False) -> None:
    """Copy bytes until the reader ends, then optionally pass the end on."""
    while chunk := await reader.read(65536):
        writer.write(chunk)
        await writer.drain()
    if half_close and writer.can_write_eof():
        writer.write_eof()


async def _reply(writer: asyncio.StreamWriter, status: str) -> None:
    """Send a bodiless error response."""
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
    await writer.drain()


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    settings: DuckDBUISettings,
) -> None:
    """Rewrite the request head, then pipe both ways until either side ends."""
    try:
        head, rest = await asyncio.wait_for(_read_head(client_reader, settings.max_head_bytes), _HEAD_TIMEOUT)
        rewritten = rewrite_head(head)
    except (BadRequestError, TimeoutError) as error:
        logger.warning("Rejected a request: %s", error)
        await _reply(client_writer, "400 Bad Request")
        return
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(settings.upstream_host, settings.upstream_port)
    except OSError as error:
        logger.warning("DuckDB UI server is not reachable: %s", error)
        await _reply(client_writer, "502 Bad Gateway")
        return
    try:
        upstream_writer.write(rewritten + rest)
        to_upstream = asyncio.create_task(_pipe(client_reader, upstream_writer, half_close=True))
        to_client = asyncio.create_task(_pipe(upstream_reader, client_writer))
        tasks = [to_upstream, to_client]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if to_upstream.done() and not to_client.done():
            # The client stopped sending but may still read the response.
            await asyncio.wait([to_client], timeout=_HALF_CLOSE_GRACE)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        upstream_writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    settings: DuckDBUISettings,
) -> None:
    """Serve one client connection and always close it."""
    try:
        await _relay(client_reader, client_writer, settings)
    except OSError as error:
        logger.info("Connection ended: %s", error)
    finally:
        client_writer.close()


async def serve(settings: DuckDBUISettings | None = None) -> None:
    """Run the proxy until SIGTERM or SIGINT.

    Args:
        settings: Proxy settings. Read from the environment when omitted.
    """
    settings = settings or DuckDBUISettings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(
        lambda reader, writer: _handle(reader, writer, settings),
        host=None,
        port=settings.listen_port,
    )
    logger.info("DuckDB UI proxy listens on port %d.", settings.listen_port)
    async with server:
        await stop.wait()
