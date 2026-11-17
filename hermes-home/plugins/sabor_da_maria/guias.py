"""Prompts locais usados pelo agente; a base ilustrativa fica fora do contexto."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import analytics, negocio
from .units import norm_text

logger = logging.getLogger(__name__)
REGIOES = frozenset(regiao.lower() for _, regiao in negocio.ESTADOS.values())


def _ler(arquivo: Path) -> str:
    try:
        return arquivo.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        logger.warning("sabor_da_maria: não consegui ler o guia %s", arquivo)
        return ""


def precificacao() -> str:
    base = analytics.raiz()
    return _ler(base.parent / "analytics" / "precificacao.md") if base else ""


def pesquisa(perfil_negocio: dict[str, Any], mensagem: Any) -> list[str]:
    base = analytics.raiz()
    if base is None:
        return []
    termos = f" {norm_text(mensagem)} " if isinstance(mensagem, str) else ""
    regioes_pedidas = [r for r in sorted(REGIOES) if f" {norm_text(r)} " in termos
                      and (r not in {"norte", "sul"} or f" regiao {r} " in termos)]
    regiao_loja = negocio.localizacao(perfil_negocio)["regiao"].lower()
    regioes = regioes_pedidas or ([regiao_loja] if regiao_loja in REGIOES else [])
    arquivos = [base.parent / "pesquisa-de-receitas" / "fontes.md", base / "evidencias" / "brasil.md"]
    arquivos += [base / "evidencias" / f"{r}.md" for r in regioes]
    return [texto for arquivo in arquivos if (texto := _ler(arquivo))]
