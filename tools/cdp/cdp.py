"""CDP 极简客户端：用标准库驱动本机真实 Chrome。

本机装不上 playwright，但装了 Chrome，所以绕过去直接走
Chrome DevTools Protocol。这个文件只做「驱动浏览器」这一件事。
"""

from __future__ import annotations

import base64
import json
import socket
import struct
import time
import urllib.parse
import urllib.request

HOST, PORT = "127.0.0.1", 9222


class WS:
    """最小的 WebSocket 客户端，够 CDP 用就行。"""

    def __init__(self, url: str):
        m = __import__("re").match(r"ws://([^:/]+):(\d+)(/.*)", url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((self.host, self.port), timeout=40)
        key = base64.b64encode(bytes(16)).decode()
        handshake = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = bytes(4)
        self.sock.sendall(bytes(header) + mask +
                          bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        while True:
            if len(self._rest) >= 2:
                try:
                    return self._parse(self._rest)
                except IndexError:
                    pass
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接关闭")
            self._rest += chunk

    def _parse(self, buf: bytes) -> str:
        n = buf[1] & 0x7F
        off = 2
        if n == 126:
            if len(buf) < 4:
                raise IndexError("长度字段不完整")
            n = struct.unpack(">H", buf[2:4])[0]
            off = 4
        elif n == 127:
            if len(buf) < 10:
                raise IndexError("长度字段不完整")
            n = struct.unpack(">Q", buf[2:10])[0]
            off = 10
        payload = buf[off:off + n]
        if len(payload) < n:
            raise IndexError("需要更多数据")
        self._rest = buf[off + n:]
        return payload.decode("utf-8", "ignore")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def new_tab(url: str) -> str:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/json/new?{urllib.parse.quote(url)}",
        data=b"", method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())[
        "webSocketDebuggerUrl"]


def close_tab(ws_url: str) -> None:
    """关掉标签页，避免 probe 跑多了留一堆。"""
    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}/json/close/"
                                     + ws_url.rsplit("/", 1)[-1], method="GET")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def cdp(ws: WS, method: str, params: dict | None = None, timeout: float = 40) -> dict:
    mid = int(time.time() * 1000) % 100000
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            return msg
    raise TimeoutError(f"CDP 超时: {method}")


def evaluate(ws: WS, expr: str) -> object:
    res = cdp(ws, "Runtime.evaluate",
              {"expression": expr, "returnByValue": True, "awaitPromise": True})
    if "error" in res:
        raise RuntimeError(str(res["error"]))
    return res.get("result", {}).get("result", {}).get("value")


def navigate(ws: WS, url: str, settle: float = 6.0) -> None:
    cdp(ws, "Page.navigate", {"url": url})
    time.sleep(settle)


def setup(ws: WS) -> None:
    cdp(ws, "Page.enable")
    cdp(ws, "Runtime.enable")
    cdp(ws, "Network.enable")
