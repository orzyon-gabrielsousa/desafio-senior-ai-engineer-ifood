"""Plugin Hermes ``sabor_da_maria``: consultoria de cardápio e precificação da Dona Maria.

Registra no Hermes Agent, sem tocar no core:

* 10 tools de domínio (toolset ``sabor_da_maria``): despensa, perfil da cozinha,
  conferência de ingredientes, CMV, cenários de preço e cardápio.
* 1 provider de ``web_extract`` (``receitas-local``): extração de receitas direto da
  página, com schema.org/Recipe, sem API externa.

Tudo em biblioteca padrão (sqlite3, zipfile, urllib, html.parser): a imagem
oficial ``nousresearch/hermes-agent`` roda este plugin sem rebuild.
"""

from __future__ import annotations

import logging

from . import tools

logger = logging.getLogger(__name__)

__version__ = "1.0.0"


def register(ctx) -> None:
    for name, schema in tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset=tools.TOOLSET,
            schema=schema,
            handler=tools.HANDLERS[name],
            description=schema["description"].split(".")[0],
            emoji="🍲",
        )
    try:
        from .extract import make_provider

        ctx.register_web_search_provider(make_provider())
    except Exception as exc:  # noqa: BLE001 - o plugin de tools continua útil mesmo sem o provider
        logger.warning("sabor_da_maria: provider de extração não registrado: %s", exc)
