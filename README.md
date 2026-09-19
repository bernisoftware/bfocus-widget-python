# bfocus-widget (Python)

> **English summary.** Official bFocus support widget for Python desktop apps: tickets, live chat
> and release notes, with the same behavior as the web widget. `pip install "bfocus-widget[qt]"`
> embeds the CDN widget in a `QWebEngineView` (PySide6); `pip install "bfocus-widget[tk]"` gives a
> fully native Tk UI (no WebView). The core has no dependencies: launcher-state polling, badge,
> version pill, acknowledgement banner, system notifications and a browser fallback. Only the
> publishable key (`bf_pk_…`) and the `userHash` computed **on your server** go into the app.
> MIT licensed.

Widget de suporte do bFocus para apps desktop em Python: **chamados, chat ao vivo e release notes**,
com o mesmo comportamento do widget web.

| Extra | O que entrega | Dependência |
|---|---|---|
| (nenhum) | núcleo: consulta do `launcher-state`, badge, pílula, banner, notificação do sistema, modo navegador, cliente da API | só a biblioteca padrão |
| `[qt]` | o widget da CDN numa `QWebEngineView` (Host Protocol v1) + botão e pílula em Qt | `PySide6` ≥ 6.5 (com QtWebEngine) |
| `[tk]` | UI nativa completa em Tk + botão e pílula em Tk | `tkinterweb` (opcional, HTML das novidades) |

Python 3.9 ou mais novo. Opcional: `Pillow` (imagens JPEG/WebP e imagem colada no editor da UI Tk).

## Instalação

```bash
pip install "bfocus-widget[qt]"   # WebView (recomendado quando o app já usa Qt)
pip install "bfocus-widget[tk]"   # UI nativa em Tk
pip install bfocus-widget         # só o núcleo (seu próprio botão ou modo navegador)
```

## Antes de começar: cadastro e `userHash`

1. No bFocus, em **Integrações → Apps nativos**, cadastre o app com o `app_id` que você vai usar
   (ex.: `com.empresa.erp`). Ele vira a origem `app://com.empresa.erp`.
2. Calcule o `userHash` **no seu servidor**, nunca no app:

```python
# no SEU backend, com o SDK de servidor do bFocus (o segredo bf_whs_… fica só lá)
user_hash = client.sign_widget_identity(user_external_id, customer_external_id)
# = hex(HMAC-SHA256(bf_whs_…, "v1:" + user_external_id + ":" + customer_external_id))
```

> **Segurança.** No app vão **só** a chave pública `bf_pk_…` e o `userHash`. Nunca `bf_whs_…`,
> `bf_live_…` ou `bf_sk_…` (o pacote recusa essas chaves). A origem `app://…` é só um rótulo:
> a proteção real é o `userHash` com **Exigir sessão verificada** ligado no bFocus.

## Uso com Qt (WebView)

```python
# Importe bfocus_widget.qt ANTES de criar o QApplication (exigência do QtWebEngine).
from bfocus_widget.qt import BFocusQtHost, BFocusLauncherButton, BFocusReleaseBadge
from PySide6.QtWidgets import QApplication
from bfocus_widget import BFocusConfig, BFocusWidget, User, Customer

app = QApplication([])
widget = BFocusWidget(
    BFocusConfig(
        publishable_key="bf_pk_…",
        app_id="com.empresa.erp",
        user=User(external_id="USR-123", name="Ana Souza", email="ana@empresa.com.br"),
        customer=Customer(external_id="ACME-001", name="Acme Ltda"),
        user_hash=hash_do_seu_servidor,          # ou user_hash_provider=funcao_que_busca_o_hash
        product="erp",                           # opcional
        notifications=True,                      # notificação do sistema quando o "•" acende
    ),
    on_badge_changed=lambda label: ...,          # '' | '•' | '3' | '99+'
    on_release_notes_changed=lambda st: ...,     # st.label ('v4.2.0' | '—'), st.dot, st.banner_ids
    on_error=lambda code, detail: ...,
)
host = BFocusQtHost(widget)                      # painel 400 × 620 (ou BFocusQtHost(widget, parent))
button = BFocusLauncherButton(widget)            # botão redondo de 56 px com badge (opcional)
pill = BFocusReleaseBadge(widget)                # pílula de versão; clicar abre o histórico
widget.start()                                   # = init: primeira consulta e polling
app.exec()
```

O host Qt:
- carrega `https://widget.bfocus.com.br/v1/embed.html#…` (parâmetros no fragmento) e mantém a
  WebView viva entre aberturas (`bfocus:open`/`bfocus:close` liga e desliga o stream do chat);
- fala com o embed por `QWebChannel` (`window.bFocusHost`) e só aceita mensagens da origem do embed;
- trava a navegação na origem do embed: qualquer outro link abre no navegador do sistema;
- baixa anexos com "salvar como" (`bfocus:download` e `<a download>`); o seletor de arquivos
  (`<input type=file multiple>`) é o nativo do QtWebEngine;
- mostra "carregando" até o `bfocus:ready` e uma tela "sem conexão" com "tentar de novo" (e nova
  tentativa automática a cada 15 s);
- abre o banner de ciência num diálogo modal do tamanho da tela que não fecha por Esc nem pelo
  botão da janela; fecha sozinho no `bfocus:rn:done`.

## Uso com Tk (UI nativa)

```python
import tkinter as tk
from bfocus_widget import BFocusConfig, BFocusWidget, User, Customer
from bfocus_widget.tk import BFocusTkHost, BFocusTkLauncherButton, BFocusTkReleaseBadge

root = tk.Tk()
widget = BFocusWidget(BFocusConfig(publishable_key="bf_pk_…", app_id="com.empresa.erp",
                                   user=User("USR-123"), customer=Customer("ACME-001"),
                                   user_hash=hash_do_seu_servidor))
BFocusTkHost(widget, root)
BFocusTkLauncherButton(root, widget).place(relx=1, rely=1, x=-20, y=-20, anchor="se")
BFocusTkReleaseBadge(root, widget).pack()
widget.start()
root.mainloop()
```

A UI Tk segue o `widget-behavior-spec.md` do bFocus: lista com status e não lidos, novo chamado com
anexos, detalhe com conversa, reabertura e aviso de encerrado, chat ao vivo (fila, atendente,
assistente com "falar com um atendente", envio otimista com "tentar de novo"/"descartar",
agrupamento de 2 min, encerrar, CSAT, continuar), splash de novidades, banner com trava de rolagem
e histórico. Toda a regra mora em view-models testáveis sem janela (`bfocus_widget.tk.viewmodels`).

Diferenças aceitas (spec §10) e limites do Tk:
- **Editor rico:** negrito, itálico, riscado, lista, lista numerada, citação, código e link (campo sob
  a barra: Enter aplica, Esc cancela, vazio remove, sem esquema vira `https://`). Imagem colada só
  com Pillow instalado. A formatação vale para o texto selecionado.
- **HTML:** desenhado num `tk.Text` com estilos; no conteúdo das novidades usa o `tkinterweb` se
  estiver instalado.
- **Arrastar e soltar** arquivos e **colar arquivos** não existem no Tk puro: use o botão 📎.
- Prévia de imagem: PNG/GIF sem dependências; outros formatos com Pillow.

## API

`BFocusConfig` (snake_case; `BFocusConfig.from_dict` aceita também os nomes do contrato em camelCase):

| Campo | Padrão | |
|---|---|---|
| `publishable_key` | — | `bf_pk_…` (obrigatório) |
| `app_id` | — | obrigatório no desktop; origem `app://<app_id em minúsculas>` |
| `user`, `customer` | — | `User(external_id, name?, email?, phone?)`, `Customer(external_id, name?, document?, email?, phone?, website?)` (ou dicts) |
| `user_hash` / `user_hash_provider` | — | hash do seu servidor; o provider (função sync ou async) é chamado no `start()` e após `WIDGET_USER_HASH_INVALID` |
| `product`, `audience` | — | slug do produto; `external` \| `internal` \| `both` |
| `locale` | idioma do sistema | `pt*` → `pt_BR`, `es*` → `es`, resto → `en` |
| `show_release_notes` | `True` | splash de novidades dentro dos chamados |
| `auto_show_release_banner` | `True` | abre o banner de ciência sozinho |
| `api_base_url`, `embed_base_url` | produção | homologação/testes (`http://` só em 127.0.0.1/localhost) |
| `poll_interval_seconds` | `60` | consulta do `launcher-state` com o widget fechado |
| `notifications` | `False` | notificação do sistema quando o "•" acende |
| `storage_dir` | dir. de dados do usuário | onde gravar `last_seen` e o "lido" dos chamados |

`BFocusWidget`: `start()` (= `init`), `open(target=None)` (`'list'`, `'new'`, `'chat'`,
`('ticket', id)`), `close()`, `open_release_notes_history()`, `refresh()`, `logout()`,
`update_identity(user=, customer=, user_hash=)`, `register_push_token(token, platform)`,
`handle_push(data) -> bool`, `shutdown()`. Propriedades: `badge_label`, `release_notes`,
`primary_color`, `is_open`, `last_state`. Callbacks: `on_badge_changed`, `on_release_notes_changed`,
`on_error(code, detail)`, `on_open`, `on_close` (sempre na thread da interface quando há host).

### Comportamento
- **Consulta:** a primeira chamada sai no `start()` e nada mais corre em paralelo com ela (ela cria o
  usuário). Depois, a cada `poll_interval_seconds` enquanto o app estiver aberto e o widget fechado;
  fechar o widget consulta na hora. Erro de rede e 5xx são ignorados em silêncio.
- **Erros (`on_error`):** 401 de identidade com `user_hash_provider` pede um hash novo uma vez e
  repete; só avisa se a repetição também for recusada (sem provider, avisa na hora). Outros 4xx
  chegam com o código do envelope ou `HTTP_<status>`. Cada código é avisado uma vez até um sucesso.
  Do embed chegam também `WIDGET_CONFIG_FAILED` e os de identidade.
- **Badge:** na primeira visita só grava a base; depois acende "•" quando há evento novo (comparado
  como instante). Com o widget aberto, mostra os não lidos (`99+` acima de 99).
- **Modo navegador:** sem host (ou sem WebView), `open()` abre o navegador padrão com
  `host=browser`. Badge e pílula continuam pelo REST; o banner **não** abre sozinho: a pílula acende
  o ponto e o banner aparece quando o usuário abrir as novidades.
- **Notificação do sistema** (melhor esforço, sem dependências): macOS via `osascript`, Linux via
  `notify-send` (o clique abre o widget com libnotify ≥ 0.7.9), Windows via toast do PowerShell.
  No macOS e no Windows o clique não volta ao app; passe `notifier=` ao `BFocusWidget` para usar o seu.
- **Push:** o desktop não tem FCM; `register_push_token`/`handle_push` existem para apps híbridos
  que já recebem o token e o `data` do FCM.
- **`logout()`:** para a consulta, cancela o push, apaga o estado local e os dados do embed (Qt).

## Testes

```bash
cd widgets-native/python
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[test,qt,tk]"
.venv/bin/python -m pytest -q --ignore=tests/test_qt_host.py   # núcleo, integração e Tk
.venv/bin/python -m pytest -q tests/test_qt_host.py            # Qt (offscreen)
```

Os testes rodam todos os casos de `tests/scenarios.json` (cópia gerada por
`node widgets-native/conformance/generate.mjs`; não edite) e sobem o servidor simulado
(`node … mock-server.mjs 0`; sem `node`, a integração é pulada). Qt e Tk rodam em processos
separados (os dois laços de eventos não convivem num processo no macOS).

Textos e cores vêm do widget web: depois de mudar `widget/src/shared/`, rode
`python widgets-native/python/scripts/sync_shared.py` (também copia o kit de conformidade para
`tests/conformance/`, usado no CI do espelho).

## Licença

MIT — Copyright (c) 2026 Berni Software.
