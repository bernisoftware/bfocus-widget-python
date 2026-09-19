#!/usr/bin/env python3
"""Copia os textos e as cores do widget web para dentro do pacote.

O pacote vira um repositório público separado (espelho) e não pode importar nada de fora
do próprio diretório. Por isso `widget/src/shared/i18n/*.json` e `widget/src/shared/tokens.json`
(a fonte única de textos e cores do web e dos nativos) são copiados para
`src/bfocus_widget/shared/`.

Uso (a partir do monorepo):
    python widgets-native/python/scripts/sync_shared.py          # copia
    python widgets-native/python/scripts/sync_shared.py --check  # falha se a cópia estiver velha
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
SRC = PKG.parent.parent / "widget" / "src" / "shared"
DEST = PKG / "src" / "bfocus_widget" / "shared"

FILES = {
    "i18n/pt_BR.json": "i18n/pt_BR.json",
    "i18n/en.json": "i18n/en.json",
    "i18n/es.json": "i18n/es.json",
    "tokens.json": "tokens.json",
}

# Kit de conformidade: o espelho público não tem widgets-native/conformance, então o CI de lá
# sobe o servidor simulado desta cópia (tests/conformance/). A cópia oficial de scenarios.json
# para os testes continua sendo tests/scenarios.json (generate.mjs).
CONFORMANCE = PKG.parent / "conformance"
KIT = PKG / "tests" / "conformance"
KIT_FILES = ("mock-server.mjs", "mock-embed.html", "scenarios.json")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    if not SRC.is_dir():
        # No espelho público o monorepo não existe: a cópia versionada é a verdade.
        print(f"fonte não encontrada ({SRC}); nada a sincronizar")
        return 0
    stale = []
    pairs = [(SRC / s, DEST / d) for s, d in FILES.items()]
    pairs += [(CONFORMANCE / f, KIT / f) for f in KIT_FILES if (CONFORMANCE / f).exists()]
    for src, dest in pairs:
        src_rel = src.name
        data = src.read_bytes()
        if dest.exists() and dest.read_bytes() == data:
            continue
        if check:
            stale.append(str(dest))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"copiado {src_rel} -> {dest.relative_to(PKG)}")
    if stale:
        print("cópia desatualizada:\n  " + "\n  ".join(stale) + "\nRode: python widgets-native/python/scripts/sync_shared.py")
        return 1
    if check:
        print("textos e cores em dia")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
