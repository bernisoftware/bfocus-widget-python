"""Smoke da UI Tk: cria e destrói todas as janelas e telas contra a API simulada."""
from __future__ import annotations

import time
import tkinter as tk

import pytest
from conftest import config_for
from test_tk_viewmodels import FakeApi, note

from bfocus_widget import __version__
from bfocus_widget.core.http import ApiError, NetworkError
from bfocus_widget.core.storage import StateStore
from bfocus_widget.core.widget import BFocusWidget

pytestmark = pytest.mark.tk

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


class SmokeApi(FakeApi):
    def chat_stream_ticket(self, cid):
        raise ApiError(404, "CONVERSATION_NOT_FOUND")  # fatal: o stream para sem rede

    def fetch_bytes(self, url, timeout=0):
        return PNG_1PX

    def download_to(self, url, dest):
        with open(dest, "wb") as f:
            f.write(b"%PDF-1.4")
        return dest


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk sem display")
    r.withdraw()
    errors = []
    r.report_callback_exception = lambda *a: errors.append(a)
    r.errors = errors
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def pump(root, pred=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if pred is None or pred():
            return True
        time.sleep(0.01)
    return pred() if pred else True


def texts(w):
    out = []
    try:
        if isinstance(w, tk.Label):
            out.append(str(w.cget("text")))
        elif isinstance(w, tk.Text):
            out.append(w.get("1.0", "end-1c"))
        elif isinstance(w, tk.Canvas):
            out += [str(w.itemcget(i, "text")) for i in w.find_all() if w.type(i) == "text"]
    except tk.TclError:
        pass
    for c in w.winfo_children():
        out += texts(c)
    return out


def has(w, s):
    return any(s in x for x in texts(w))


def test_every_window_builds_and_closes(root, tmp_path):
    from bfocus_widget.tk import BFocusTkHost, BFocusTkLauncherButton, BFocusTkReleaseBadge

    api = SmokeApi()
    api.release_notes = [note("x", False)]
    api.pending = [note("a", True), note("b", False)]
    n1 = note("a", True, "4.2.0")
    n1["is_current"] = True
    api.changelog = {"products": [{"current_version": "4.2.0"}], "notes": [n1, note("b", False, "4.1.0")]}
    widget = BFocusWidget(config_for("min", client=f"python/{__version__}"), store=StateStore(tmp_path), notifier=lambda *a: None)
    host = BFocusTkHost(widget, root, api=api)
    host.save_path_provider = lambda name: str(tmp_path / name)
    button = BFocusTkLauncherButton(root, widget)
    pill = BFocusTkReleaseBadge(root, widget)
    button.pack()
    pill.pack()

    widget.open()  # o host é chamado pela fila da thread do Tk
    assert pump(root, lambda: host._panel is not None)
    panel = host._panel
    assert pump(root, lambda: has(panel, "Com resposta"))          # lista
    assert pump(root, lambda: has(panel, "Antes de continuar"))     # splash por cima
    assert pump(root, lambda: button.label == "1")                  # unread do widget aberto
    host._app.dismiss_splash()
    assert pump(root, lambda: not has(panel, "Antes de continuar"))
    assert has(panel, "Fale com a gente")                           # cartão do chat (online)

    host._app.select_ticket("t1")                                   # detalhe
    assert pump(root, lambda: has(panel, "Descrição") and has(panel, "Continuação de T-0"))
    host._app.page.download({"id": "a1", "filename": "log.txt"})
    assert pump(root, lambda: (tmp_path / "log.txt").exists())

    host._app.set_view({"name": "new"})                             # novo chamado
    assert pump(root, lambda: has(panel, "Criar chamado") and has(panel, "Departamento de destino"))

    host._app.open_chat()                                           # chat
    assert pump(root, lambda: has(panel, "Nova conversa"))
    host._app.page.composer.set_text("oi")
    host._app.page.send()
    assert pump(root, lambda: has(panel, "Protocolo T-5"))
    host._app.page.request_end()
    assert pump(root, lambda: has(panel, "Encerrar esta conversa?"))
    host._app.page.end()
    assert pump(root, lambda: has(panel, "Como foi o atendimento?"))  # CSAT
    host._app.csat_for("t5").set_rating(5)
    assert pump(root, lambda: has(panel, "Enviar avaliação"))

    widget.close()
    assert pump(root, lambda: panel.state() == "withdrawn")

    host.show_banner(["a", "b"])                                    # banner com ciência
    banner = host._banner
    assert pump(root, lambda: has(banner, "Li e estou ciente das novas funções e ajustes"))
    assert pump(root, lambda: not banner.vm.ack_disabled, 3)         # texto curto: libera na hora
    banner.vm.confirm()
    assert pump(root, lambda: has(banner, "Entendi"))
    banner.vm.confirm()
    assert pump(root, lambda: host._banner is None)                 # rn:done fecha

    widget.open_release_notes_history()                             # histórico
    assert pump(root, lambda: host._history is not None and has(host._history, "Versão atual"))
    host._history.vm.close()
    assert pump(root, lambda: host._history.state() == "withdrawn")

    widget.notify_branding("#123456")
    assert pump(root, lambda: button.color == "#123456" and pill.color == "#123456")

    widget.logout()
    assert pump(root, lambda: host._panel is None)
    assert not root.errors, root.errors
    assert not host.runner.errors, host.runner.errors
    button.destroy()
    pill.destroy()


def test_splash_ack_gate(root, tmp_path):
    """Ciência exigida dentro dos chamados: trava sem pular, erro visível, depois os cartões."""
    from bfocus_widget.tk import BFocusTkHost

    api = SmokeApi()
    api.release_notes = [note("g", True), note("x", False)]
    widget = BFocusWidget(config_for("min", client=f"python/{__version__}"), store=StateStore(tmp_path), notifier=lambda *a: None)
    host = BFocusTkHost(widget, root, api=api)
    widget.open()
    assert pump(root, lambda: host._panel is not None)
    panel = host._panel
    assert pump(root, lambda: has(panel, "CIÊNCIA EXIGIDA") and has(panel, "Li e estou ciente"))
    assert not has(panel, "Pular por agora")                        # sem pular
    vm = host._app.splash
    assert pump(root, lambda: not vm.ack_disabled, 3)                # texto curto: libera na hora
    api.fail["acknowledge"] = NetworkError("network")
    vm.acknowledge()
    assert pump(root, lambda: has(panel, "Não foi possível registrar"))
    del api.fail["acknowledge"]
    vm.acknowledge()
    assert pump(root, lambda: has(panel, "Antes de continuar") and has(panel, "Pular por agora"))
    host._app.dismiss_splash()
    assert pump(root, lambda: not has(panel, "Antes de continuar"))
    api.release_notes.append(note("g2", True))
    host.app_focused()                                               # voltou ao foco: confere na hora
    assert pump(root, lambda: has(panel, "CIÊNCIA EXIGIDA"))         # trava de novo depois do "Pular"
    widget.logout()
    assert pump(root, lambda: host._panel is None)
    assert not root.errors, root.errors
    assert not host.runner.errors, host.runner.errors


def test_rich_editor_link_field(root):
    """Link: campo sob a barra (sem diálogo). Enter aplica, Esc cancela, vazio remove,
    sem esquema vira https://, e "remover link" só aparece quando já há link."""
    from bfocus_widget.shared import Translator
    from bfocus_widget.tk.views.html import RichEditor

    changes = []
    ed = RichEditor(root, Translator("pt_BR"), on_change=changes.append)
    ed.pack()
    ed.text.insert("1.0", "veja o site")
    ed.text.tag_add("sel", "1.7", "1.11")
    ed._link()
    assert pump(root, lambda: ed._link_bar.winfo_manager())
    assert not ed._link_remove.winfo_manager()          # ainda sem link
    # Enter aplica e Esc cancela (ligados no campo); com a janela escondida o evento de tecla
    # sintético iria para a janela com foco, então chamamos o que eles chamam.
    assert ed._link_entry.bind("<Return>") and ed._link_entry.bind("<Escape>")
    ed._link_var.set("bfocus.com.br")
    ed.apply_link()
    pump(root)
    assert ed.to_html() == '<p>veja o <a href="https://bfocus.com.br">site</a></p>'
    assert not ed._link_bar.winfo_manager()

    ed.text.tag_add("sel", "1.7", "1.11")
    ed._link()
    pump(root)
    assert ed._link_var.get() == "https://bfocus.com.br" and ed._link_remove.winfo_manager()
    ed._link_var.set("javascript:alert(1)")
    ed.apply_link()
    assert "bfocus.com.br" in ed.to_html() and ed._link_bar.winfo_manager()  # esquema recusado
    ed.cancel_link()
    pump(root)
    assert not ed._link_bar.winfo_manager()               # Esc cancela sem mudar nada

    ed.text.tag_add("sel", "1.7", "1.11")
    ed._link()
    ed._link_var.set("")
    ed.apply_link()                                          # vazio remove
    assert ed.to_html() == "<p>veja o site</p>"
    ed.destroy()
