"""Bounded public HTTPS GETs. No browser profile, cookies, JS or model-chosen URLs."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BYTES = 1_500_000
MAX_TEXT = 24000
PROXY_HOSTS = {"source.android.com", "developer.android.com"}


def is_public(address):
    ip = ipaddress.ip_address(address)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("网页地址必须是标准公开 HTTPS URL。")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.port not in {None, 443} or url.query):
        raise ValueError("首版只读取无账号、密码、查询参数的 HTTPS 网页；不接带令牌/登录链接。")
    host = url.hostname.encode("idna").decode("ascii").lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("不读取本机或内部地址。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public(address):
            raise ValueError("不读取私网、回环或保留地址。")
    return urlunsplit(("https", host, url.path or "/", "", ""))


def resolve_public(host):
    rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = sorted({row[4][0] for row in rows})
    if not addresses or any(not is_public(ip) for ip in addresses):
        raise ValueError("DNS 含非公网地址；拒绝连接。")
    # Prefer IPv4 when both exist; never resolve the hostname again at connect.
    return sorted(addresses, key=lambda ip: (ipaddress.ip_address(ip).version, ip))[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    """Connect only to the checked numeric IP; retain hostname TLS verification."""
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def local_proxy(value):
    """Only an explicitly configured loopback HTTP CONNECT proxy; no credentials."""
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username is not None or url.password is not None or url.path not in {"", "/"}
            or url.query or url.fragment):
        raise ValueError("WELLPHONE_WEB_PROXY 仅接受 http://127.0.0.1:端口，不含凭据或其他路径。")
    return url.port


def connect_to(host, timeout):
    proxy = os.environ.get("WELLPHONE_WEB_PROXY", "")
    if proxy:
        port = local_proxy(proxy)
        if host not in PROXY_HOSTS:
            raise ValueError("代理模式只允许 source.android.com 与 developer.android.com；不把任意域名交给代理。")
        # HTTPSConnection wraps TLS *after* CONNECT, validating the target host,
        # not the loopback proxy. DNS resolution is delegated to this trusted
        # local proxy only for the two fixed official hosts.
        connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                               context=ssl.create_default_context())
        connection.set_tunnel(host, 443)
        return connection
    address = resolve_public(host)
    return PinnedHTTPS(host, address, timeout)


def fetch(url):
    current, visited = public_url(url), set()
    deadline = time.monotonic() + 30
    for _ in range(4):
        if current in visited or time.monotonic() >= deadline:
            raise ValueError("网页重定向循环或读取超时。")
        visited.add(current)
        parsed = urlsplit(current)
        timeout = max(.1, min(10, deadline - time.monotonic()))
        connection = connect_to(parsed.hostname, timeout)
        try:
            connection.request("GET", parsed.path or "/", headers={
                "User-Agent": "Wellphone-Study/0.1", "Accept": "text/html,text/plain",
                "Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                destination = response.getheader("Location")
                if not destination:
                    raise ValueError("重定向没有目标。")
                redirected = public_url(urljoin(current, destination))
                if urlsplit(redirected).hostname != parsed.hostname:
                    raise ValueError("跨域重定向未获授权；请直接提供最终公开网页地址。")
                current = redirected
                continue
            if response.status != 200:
                raise ValueError(f"网页返回 HTTP {response.status}；不登录、不绕过访问限制。")
            content_type = response.getheader("Content-Type", "").lower()
            if content_type.split(";", 1)[0].strip().lower() not in {"text/html", "text/plain"}:
                raise ValueError("首版只读取 HTML/纯文本；PDF、附件和动态登录页不自动转换。")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("未请求的压缩响应，停止读取。")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
                raise ValueError("网页大小不符合上限。")
            body = bytearray()
            while len(body) <= MAX_BYTES:
                if time.monotonic() >= deadline:
                    raise ValueError("网页读取超时。")
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("网页超过大小上限。")
            charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
            encoding = charset[1].lower() if charset else "utf-8"
            if encoding not in {"utf-8", "utf8", "us-ascii", "gbk", "gb2312", "gb18030"}:
                raise ValueError("网页字符编码不受支持。")
            return current, bytes(body).decode(encoding, errors="strict"), content_type, hashlib.sha256(body).hexdigest()
        finally:
            connection.close()
    raise ValueError("网页重定向次数超过上限。")


class TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "pre", "tr", "br"}
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "template", "form"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "embed", "param", "track", "col"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.all, self.main, self.title = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skip = tag in self.SKIP or "hidden" in attrs or attrs.get("aria-hidden") == "true"
        primary = tag in {"main", "article"} or attrs.get("role") == "main" or "devsite-article-body" in attrs.get("class", "").split()
        if tag not in self.VOID:
            self.stack.append((tag, skip, primary))
        if tag in self.BLOCKS:
            self.boundary()
        elif tag in {"td", "th"}:
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag in self.BLOCKS:
            self.boundary()
        elif tag in {"td", "th"}:
            self.handle_data(" ")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def append(self, data):
        if any(item[1] for item in self.stack):
            return
        if any(item[0] == "title" for item in self.stack):
            self.title.append(data)
            return
        self.all.append(data)
        if any(item[2] for item in self.stack):
            self.main.append(data)

    def boundary(self):
        # Only structural markup creates a paragraph boundary. HTML source
        # wrapping inside a sentence or around inline <code>/<a> must not.
        self.append("\n")

    def handle_data(self, data):
        self.append(re.sub(r"\s+", " ", data))


def paragraph_pieces(paragraph, limit=1200):
    """Prefer complete sentences, then word boundaries for very long blocks."""
    while len(paragraph) > limit:
        window = paragraph[:limit + 1]
        ends = [m.end() for m in re.finditer(r"[。！？][’”\"']?|[.!?][’”\"']?(?=\s|$)", window)
                if m.end() <= limit]
        cut = ends[-1] if ends else window.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit  # An overlong token has no semantic splitting point.
        piece = paragraph[:cut].strip()
        if piece:
            yield piece
        paragraph = paragraph[cut:].strip()
    if paragraph:
        yield paragraph


def extract(html, content_type):
    title = "公开技术资料"
    if content_type.startswith("text/html"):
        parser = TextExtractor()
        parser.feed(html)
        parser.close()
        title = " ".join("".join(parser.title).split())[:200] or title
        body = "".join(parser.main or parser.all)
        blocks = body.splitlines()
    else:
        # Plain text commonly wraps at 80 columns; blank lines delimit blocks.
        blocks = re.split(r"\n\s*\n", html.replace("\r\n", "\n").replace("\r", "\n"))
    paragraphs = [" ".join(block.split()) for block in blocks]
    paragraphs = [p for p in paragraphs if p]
    if sum(map(len, paragraphs)) < 120:
        raise ValueError("可读正文太少；可能依赖登录或 JavaScript，不伪装成读取成功。")
    result, used = [], 0
    for paragraph in paragraphs:
        for piece in paragraph_pieces(paragraph):
            if used + len(piece) > MAX_TEXT:
                return title, result, True
            result.append(piece)
            used += len(piece)
    return title, result, False


def read_source(url, index):
    final, html, content_type, body_hash = fetch(url)
    title, paragraphs, truncated = extract(html, content_type)
    return {"id": f"S{index}", "url": final, "title": title, "body_sha256": body_hash,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "truncated": truncated,
            "paragraphs": [{"id": f"S{index}:P{i}", "text": p} for i, p in enumerate(paragraphs, 1)]}
