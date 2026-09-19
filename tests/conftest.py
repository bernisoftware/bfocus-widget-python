"""Fixtures comuns: casos de scenarios.json e o servidor simulado do kit de conformidade."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG / "src"))

from bfocus_widget.core.config import BFocusConfig  # noqa: E402

SCENARIOS = json.loads((HERE / "scenarios.json").read_text("utf-8"))

# No monorepo o servidor simulado mora em widgets-native/conformance; no espelho público, na
# cópia tests/conformance/ (scripts/sync_shared.py). BFOCUS_MOCK_SERVER força outro caminho.
_MONOREPO_MOCK = PKG.parent / "conformance" / "mock-server.mjs"
MOCK_SERVER = Path(
    os.environ.get("BFOCUS_MOCK_SERVER")
    or (_MONOREPO_MOCK if _MONOREPO_MOCK.exists() else HERE / "conformance" / "mock-server.mjs")
)


def config_for(name: str, **overrides) -> BFocusConfig:
    data = dict(SCENARIOS["configs"][name])
    data.setdefault("locale", SCENARIOS["defaults"]["locale"])
    data.update(overrides)
    return BFocusConfig.from_dict(data)


class MockServer:
    def __init__(self, base: str, proc: subprocess.Popen) -> None:
        self.base = base
        self.proc = proc

    def _post(self, path: str, body: object = None) -> None:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()

    def reset(self) -> None:
        self._post("/__reset")

    def scenario(self, name: str) -> None:
        self._post("/__scenario", {"name": name})

    def log(self) -> dict:
        with urllib.request.urlopen(self.base + "/__log", timeout=5) as r:
            return json.loads(r.read())


@pytest.fixture(scope="session")
def scenarios() -> dict:
    return SCENARIOS


@pytest.fixture(scope="session")
def mock_server():
    node = shutil.which("node")
    if not node:
        pytest.skip("node não encontrado: testes de integração pulados")
    if not MOCK_SERVER.exists():
        pytest.skip(f"servidor simulado ausente: {MOCK_SERVER}")
    proc = subprocess.Popen([node, str(MOCK_SERVER), "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = proc.stdout.readline() if proc.stdout else ""
    m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
    if not m:
        proc.kill()
        pytest.fail(f"mock-server não subiu: {line!r} {proc.stderr.read() if proc.stderr else ''}")
    server = MockServer(f"http://127.0.0.1:{m.group(1)}", proc)
    try:
        yield server
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def server(mock_server: MockServer) -> MockServer:
    mock_server.reset()
    return mock_server
