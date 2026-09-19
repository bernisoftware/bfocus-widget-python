# Changelog

Todas as mudanças relevantes deste pacote. Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versões: [SemVer](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-09-15

Primeira versão.

### Adicionado
- Núcleo sem dependências (`bfocus_widget`): `BFocusWidget`/`BFocusConfig`, consulta do
  `launcher-state` (primeira chamada serializada, depois a cada `poll_interval_seconds` com o
  widget fechado), badge (`''`, `'•'`, `'N'`, `'99+'`) com `last_seen` persistente, pílula de
  versão, banner de ciência, `userHashProvider` com nova tentativa, modo navegador, notificação do
  sistema (`notifications=True`), `register_push_token`/`handle_push` e `logout()`.
- Cliente da API do widget (`bfocus_widget.client`): chamados, anexos, release notes e chat ao vivo
  com SSE (ticket de uso único, reconexão exponencial, deduplicação) e modo poll.
- Extra `[qt]` (PySide6): `BFocusQtHost` com `QWebEngineView` + `QWebChannel` (Host Protocol v1),
  navegação travada na origem do embed, downloads, banner modal que não fecha, histórico,
  `BFocusLauncherButton` e `BFocusReleaseBadge`.
- Extra `[tk]`: UI nativa completa em Tk seguindo o `widget-behavior-spec.md` (lista, novo chamado,
  detalhe, chat com fila de envio e CSAT, splash de novidades por usuário com a ciência exigida
  travando o widget como no web, banner com trava de rolagem, histórico),
  `BFocusTkLauncherButton` e `BFocusTkReleaseBadge`.
- Textos em pt_BR, en e es e cores copiados do widget web (`scripts/sync_shared.py`).
