"""Notificação do sistema no desktop (BRIEF §4.5), melhor esforço e sem dependências:

- macOS: `osascript` (`display notification`). O clique não volta para o app.
- Linux: `notify-send`. Com libnotify >= 0.7.9 (`--action`), o clique abre o widget.
- Windows: toast via PowerShell (API WinRT). O clique não volta para o app.

Qualquer falha é silenciosa: notificação nunca derruba o widget. Quem precisar de clique em
todas as plataformas pode passar o próprio `notifier` ao `BFocusWidget`.
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional
from xml.sax.saxutils import escape as xml_escape

log = logging.getLogger("bfocus_widget")

_TEXTS = {
    "pt_BR": ("Suporte", "Há novidades nos seus chamados."),
    "en": ("Support", "There are updates on your tickets."),
    "es": ("Soporte", "Hay novedades en tus tickets."),
}

Notifier = Callable[[str, str, Optional[Callable[[], None]]], None]


def notification_text(locale: str) -> tuple:
    return _TEXTS.get(locale, _TEXTS["pt_BR"])


def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run(cmd: list, **kw) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("bfocus: notificação falhou: %s", e)
        return None


def _mac(title: str, body: str) -> None:
    _run(["osascript", "-e", f"display notification {_applescript_str(body)} with title {_applescript_str(title)}"], timeout=10)


def _linux(title: str, body: str, on_click: Optional[Callable[[], None]]) -> None:
    exe = shutil.which("notify-send")
    if not exe:
        return
    if on_click:
        help_out = _run([exe, "--help"], timeout=5)
        if help_out and "--action" in (help_out.stdout or ""):
            # --wait bloqueia até o usuário clicar ou a notificação sumir: roda na thread própria.
            res = _run([exe, "--app-name=bFocus", "--wait", "--action=default=Abrir", title, body], timeout=600)
            if res and (res.stdout or "").strip() == "default":
                on_click()
            return
    _run([exe, "--app-name=bFocus", title, body], timeout=10)


def _windows(title: str, body: str) -> None:
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{xml_escape(title)}</text>"
        f"<text>{xml_escape(body)}</text></binding></visual></toast>'.Replace('&apos;', \"'\"));"
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
        # AppId do próprio PowerShell: sempre registrado, dispensa atalho no Menu Iniciar.
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=20, creationflags=flags)


def system_notifier(title: str, body: str, on_click: Optional[Callable[[], None]] = None) -> None:
    """Mostra a notificação numa thread própria (nunca bloqueia quem chamou)."""

    def run() -> None:
        try:
            if sys.platform == "darwin":
                _mac(title, body)
            elif os.name == "nt":
                _windows(title, body)
            else:
                _linux(title, body, on_click)
        except Exception as e:  # noqa: BLE001
            log.debug("bfocus: notificação falhou: %s", e)

    threading.Thread(target=run, name="bfocus-notify", daemon=True).start()
