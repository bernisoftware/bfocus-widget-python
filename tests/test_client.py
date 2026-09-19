"""Cliente da API e tempo real do chat contra um servidor HTTP local (stdlib)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import config_for

from bfocus_widget.client.api import WidgetApi
from bfocus_widget.client.chat_stream import ChatStream, is_fatal
from bfocus_widget.client.sse import SseParser
from bfocus_widget.core.http import ApiError


class Fake:
    """Estado do servidor falso, configurável por teste."""

    def __init__(self):
        self.mode = "sse"
        self.ticket_status = 200
        self.tickets_issued = 0
        self.stream_requests = []   # (ticket, last_event_id)
        self.poll_requests = []     # after
        self.sse_scripts = []       # lista de listas de linhas SSE, uma por conexão
        self.poll_events = {}       # after → lista de eventos
        self.events_failures = 0
        self.uploads = []
        self.lock = threading.Lock()
        self.release = threading.Event()


def make_handler(fake: Fake):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silêncio
            pass

        def _json(self, status, data):
            body = json.dumps({"code": "OK" if status < 400 else "ERROR", "data": data if status < 400 else None,
                               "message": "" if status < 400 else data}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            path = urlsplit(self.path).path
            if path == "/api/v1/widget/chat/stream-ticket":
                if fake.ticket_status != 200:
                    return self._json(fake.ticket_status, "CONVERSATION_NOT_FOUND")
                with fake.lock:
                    fake.tickets_issued += 1
                    t = f"ticket-{fake.tickets_issued}"
                return self._json(200, {"mode": fake.mode, "ticket": t, "expires_in": 60,
                                        "poll_interval_ms": 1000, "last_event_id": 4})
            if path == "/api/v1/widget/upload":
                fake.uploads.append((self.headers.get("Content-Type"), raw))
                return self._json(200, {"url": "https://cdn/x.txt", "filename": "nota.txt", "size": 5, "mime": "text/plain"})
            if path.endswith("/messages"):
                return self._json(201, {"id": "m1", "content": json.loads(raw)["content"]})
            return self._json(404, "NOT_FOUND")

        def do_GET(self):
            parts = urlsplit(self.path)
            q = parse_qs(parts.query)
            if parts.path == "/api/v1/stream":
                with fake.lock:
                    fake.stream_requests.append((q.get("ticket", [None])[0], q.get("last_event_id", [None])[0]))
                    script = fake.sse_scripts.pop(0) if fake.sse_scripts else None
                if script is None:
                    return self._json(401, "STREAM_TICKET_INVALID")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in script:
                    if chunk == "HOLD":
                        fake.release.wait(10)  # segura a conexão aberta até o teste liberar
                        break
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
                self.close_connection = True
                return
            if parts.path.endswith("/events"):
                after = int(q.get("after", ["0"])[0])
                fake.poll_requests.append(after)
                if fake.events_failures > 0:
                    fake.events_failures -= 1
                    return self._json(503, "BUSY")
                events = fake.poll_events.get(after, [])
                last = events[-1]["id"] if events else after
                return self._json(200, {"events": events, "last_event_id": last})
            return self._json(404, "NOT_FOUND")

    return H


@pytest.fixture
def fake_server():
    fake = Fake()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    fake.base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        yield fake
    finally:
        fake.release.set()
        srv.shutdown()
        srv.server_close()


def api_for(fake):
    return WidgetApi(config_for("min", apiBaseUrl=fake.base, embedBaseUrl=fake.base + "/v1"))


def wait_until(pred, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def ev(i, typ="message.created"):
    return {"id": i, "type": typ, "created_at": "2026-09-01T10:00:00+00:00", "payload": {"message": {"id": f"m{i}"}}}


def sse(e):
    return f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"


def test_sse_parser():
    p = SseParser()
    out = [p.feed_line(line) for line in [": ping", "id: 7", "event: x", "data: a", "data: b", ""]]
    assert out[-1].data == "a\nb" and out[-1].event == "x" and out[-1].id == "7"
    assert p.feed_line("data: c") is None
    assert p.feed_line("").id == "7"  # o id persiste, como no EventSource


def test_sse_reconnects_with_new_ticket_and_dedupes(fake_server):
    fake = fake_server
    # 1ª conexão: eventos 5, 6 e o 6 repetido; o servidor fecha (fim normal).
    fake.sse_scripts = [
        [": ping\n\n", sse(ev(5)), sse(ev(6)), sse(ev(6))],
        [sse(ev(6)), sse(ev(7)), "HOLD"],
    ]
    got, statuses, connected = [], [], []
    stream = ChatStream(api_for(fake), "conv-1", got.append, statuses.append, lambda: connected.append(1), rand=lambda: 0.0)
    stream.start()
    assert wait_until(lambda: [e["id"] for e in got] == [5, 6, 7])
    assert fake.stream_requests[0] == ("ticket-1", "4")      # last_event_id vindo do ticket
    assert fake.stream_requests[1] == ("ticket-2", "6")      # nunca o mesmo ticket; último id visto
    assert len(connected) == 2                                # recarrega a cada (re)conexão
    assert "live" in statuses and "reconnecting" in statuses
    stream.stop()
    assert stream.status == "idle"
    fake.release.set()


def test_rejected_stream_ticket_reconnects(fake_server):
    fake = fake_server
    fake.sse_scripts = []  # GET /stream → 401: o EventSource trataria como erro → ticket novo
    stream = ChatStream(api_for(fake), "conv-1", lambda e: None, rand=lambda: 0.0)
    stream.start()
    assert wait_until(lambda: fake.tickets_issued >= 2, timeout=5)
    stream.stop()


def test_poll_mode_and_upgrade(fake_server):
    fake = fake_server
    fake.mode = "poll"
    fake.poll_events = {4: [ev(5)]}
    got, connected = [], []
    stream = ChatStream(api_for(fake), "conv-1", got.append, on_connected=lambda: connected.append(1), rand=lambda: 0.0)
    stream.start()
    assert wait_until(lambda: len(fake.poll_requests) >= 2, timeout=5)
    stream.stop()
    assert fake.poll_requests[:2] == [4, 5]
    assert [e["id"] for e in got] == [5]
    assert connected == [1]
    # A cada N segundos em poll, tenta voltar ao SSE: pede ticket novo.
    fake.poll_requests.clear()
    issued = fake.tickets_issued
    s2 = ChatStream(api_for(fake), "conv-2", lambda e: None, poll_upgrade_seconds=0.0, rand=lambda: 0.0)
    s2.start()
    assert wait_until(lambda: fake.tickets_issued >= issued + 2, timeout=5)
    s2.stop()


def test_fatal_4xx_stops(fake_server):
    fake = fake_server
    fake.ticket_status = 404
    fatal, statuses = [], []
    stream = ChatStream(api_for(fake), "conv-x", lambda e: None, statuses.append, on_fatal=fatal.append)
    stream.start()
    assert wait_until(lambda: fatal)
    assert statuses[-1] == "failed" and not stream.running
    assert isinstance(fatal[0], ApiError) and is_fatal(fatal[0])
    assert not is_fatal(ApiError(429, "RATE_LIMITED")) and not is_fatal(ApiError(503, None))


def test_one_stream_per_conversation(fake_server):
    fake = fake_server
    fake.mode = "poll"
    a = ChatStream(api_for(fake), "same", lambda e: None)
    b = ChatStream(api_for(fake), "same", lambda e: None)
    a.start()
    b.start()
    assert not a.running and b.running
    b.stop()


def test_get_retries_once_post_does_not(fake_server):
    fake = fake_server
    api = api_for(fake)
    fake.events_failures = 1
    assert api.chat_events("c1", 0) == {"events": [], "last_event_id": 0}
    assert fake.poll_requests == [0, 0]
    fake.events_failures = 2
    with pytest.raises(ApiError) as exc:
        api.chat_events("c1", 0)
    assert exc.value.status == 503


def test_upload_multipart(fake_server, tmp_path):
    fake = fake_server
    f = tmp_path / "nota.txt"
    f.write_bytes(b"hello")
    res = api_for(fake).upload(f)
    assert res["filename"] == "nota.txt"
    ctype, raw = fake.uploads[0]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1]
    assert raw.startswith(f"--{boundary}\r\n".encode())
    assert b'name="file"; filename="nota.txt"' in raw
    assert b"Content-Type: text/plain\r\n\r\nhello\r\n" in raw
    assert raw.endswith(f"--{boundary}--\r\n".encode())


def test_chat_send_and_stream_url(fake_server):
    fake = fake_server
    api = api_for(fake)
    assert api.chat_send("c/1", "<p>oi</p>")["content"] == "<p>oi</p>"
    url = api.chat_stream_url("abc", 12)
    assert url == f"{fake.base}/api/v1/stream?ticket=abc&last_event_id=12"
    assert "user" not in url.lower()  # identidade nunca vai na URL
