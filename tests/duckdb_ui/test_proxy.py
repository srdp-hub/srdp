"""Tests for the DuckDB UI header-rewriting proxy."""

import asyncio
import socket
from collections.abc import Awaitable, Callable

import pytest

from srdp.duckdb_ui.proxy import (
    BadRequestError,
    DuckDBUISettings,
    rewrite_head,
    serve,
)


def test_rewrite_head_sets_localhost_identity() -> None:
    """Host, Origin and Referer are replaced, even when the client sent none."""
    head = b"GET /x HTTP/1.1\r\nHOST: duckdb.example.com\r\nAccept: */*"
    out = rewrite_head(head).decode()
    assert "Host: localhost:4213\r\n" in out
    assert "Origin: http://localhost:4213\r\n" in out
    assert "Referer: http://localhost:4213/\r\n" in out
    assert "example.com" not in out
    assert "Accept: */*" in out
    assert out.endswith("\r\n\r\n")


def test_rewrite_head_drops_forwarded_headers_case_insensitively() -> None:
    """The X-Forwarded-* headers never reach DuckDB."""
    head = b"GET / HTTP/1.1\r\nx-forwarded-for: 1.2.3.4\r\nX-Forwarded-Proto: https"
    assert b"forwarded" not in rewrite_head(head).lower()


def test_rewrite_head_closes_the_connection_unless_upgrading() -> None:
    """A kept-alive connection would skip the rewrite on its second request."""
    assert b"Connection: close" in rewrite_head(b"GET / HTTP/1.1\r\nConnection: keep-alive")
    upgraded = rewrite_head(b"GET / HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade")
    assert b"Connection: Upgrade" in upgraded
    assert b"Connection: close" not in upgraded


@pytest.mark.parametrize(
    "head",
    [
        b"GET / HTTP/2",
        b"get / HTTP/1.1",
        b"GET / HTTP/1.1\r\nNo colon here",
        b"GET / HTTP/1.1\r\nBad Name: x",
        b"GET / HTTP/1.1\r\n Folded: x",
    ],
)
def test_rewrite_head_rejects_malformed_input(head: bytes) -> None:
    """Malformed heads raise instead of passing through."""
    with pytest.raises(BadRequestError):
        rewrite_head(head)


def _free_port() -> int:
    """Return a TCP port that is free right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _http(port: int, request: bytes) -> bytes:
    """Send one raw request and read until the connection closes."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    data = await reader.read()
    writer.close()
    return data


async def _with_proxy(upstream_port: int, check: Callable[[int], Awaitable[None]]) -> None:
    """Run the proxy against an upstream port and call `check` with its port."""
    proxy_port = _free_port()
    settings = DuckDBUISettings(listen_port=proxy_port, upstream_host="127.0.0.1", upstream_port=upstream_port)
    task = asyncio.create_task(serve(settings))
    await asyncio.sleep(0.2)
    try:
        await check(proxy_port)
    finally:
        task.cancel()


def test_proxy_forwards_rewritten_request_and_returns_response() -> None:
    """A request reaches the upstream rewritten, and the response comes back."""
    seen: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    async def check(port: int) -> None:
        reply = await _http(port, b"GET /ddb/run HTTP/1.1\r\nHost: duckdb.example.com\r\n\r\n")
        assert reply.endswith(b"ok")
        assert b"Host: localhost:4213" in seen[0]
        assert b"example.com" not in seen[0]
        assert (await _http(port, b"nonsense\r\n\r\n")).startswith(b"HTTP/1.1 400")

    async def run() -> None:
        up_port = _free_port()
        server = await asyncio.start_server(upstream, "127.0.0.1", up_port)
        try:
            await _with_proxy(up_port, check)
        finally:
            server.close()

    asyncio.run(run())


def test_proxy_answers_502_when_upstream_is_down() -> None:
    """An unreachable DuckDB server gives a clean 502."""

    async def check(port: int) -> None:
        assert (await _http(port, b"GET / HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 502")

    asyncio.run(_with_proxy(_free_port(), check))
