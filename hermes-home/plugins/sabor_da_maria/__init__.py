"""Plugin Hermes ``sabor_da_maria``: consultoria de cardápio e precificação da Dona Maria.

Registra no Hermes Agent, sem tocar no core:

* Tools de domínio (toolset ``sabor_da_maria``): despensa, perfil da cozinha, perfil do negócio,
  conferência de ingredientes, CMV, cenários de preço, cardápio, carrinho com saldo, itens salvos e Analytics.
* 1 provider de ``web_extract`` (``receitas-local``): extração de receitas direto da
  página, com schema.org/Recipe, sem API externa.
* o passo a passo da consultora (``atividade.py``): cada ferramenta que ela usa (``web_search``, ``web_extract``, despensa, preços...)
  deixa uma frase no SQLite que a tela mostra no chat, e a pesquisa de receitas (``pesquisa.py``) segue uma ordem fixa:
  despensa → busca na internet → leitura da página → conferência → candidatas com a pergunta de feedback.

A cozinha da Dona Maria (``cozinha.py``) é um perfil só, no SQLite: a consultora pergunta na primeira conversa e ela também
marca na tela Minha cozinha; o painel do turno, as travas de aceite e os avisos do carrinho leem o mesmo registro. O negócio (onde a loja
fica, tipo de negócio e de alimentação) segue o mesmo caminho: a consultora pergunta na primeira conversa e a tela Meu negócio mostra o mesmo registro.

Tudo em biblioteca padrão (sqlite3, zipfile, urllib, html.parser): a imagem
oficial ``nousresearch/hermes-agent`` roda este plugin sem rebuild.
"""

from __future__ import annotations

import logging

from . import atividade, falas, memoria, painel, pesquisa, tools

logger = logging.getLogger(__name__)

__version__ = "1.6.0"


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
    # Hooks: (1) guardam o que a Dona Maria disse, para a trava de evidência do perfil (falas.py);
    # (2) levam o carrinho, o saldo e o perfil do negócio a todo turno, com o roteiro da pesquisa de receitas quando ela pede (painel.py);
    # (3) anotam o que cada ferramenta fez, para a tela mostrar o passo a passo (atividade.py);
    # (4) põem o próximo passo no resultado de uma busca de receita e da leitura da página (pesquisa.py).
    for hook, callback in (("pre_llm_call", falas.ao_iniciar_turno), ("pre_llm_call", painel.ao_iniciar_turno),
                           ("pre_llm_call", memoria.ao_iniciar_turno), ("post_llm_call", memoria.apos_turno),
                           ("post_tool_call", falas.apos_tool), ("post_tool_call", atividade.apos_tool),
                           ("transform_tool_result", pesquisa.depois_da_ferramenta)):
        try:
            ctx.register_hook(hook, callback)
        except Exception as exc:  # noqa: BLE001 - sem o hook, perfil_cozinha_salvar recusa por não ter a fala dela
            logger.warning("sabor_da_maria: hook %s não registrado: %s", hook, exc)
    try:
        from .extract import make_provider

        ctx.register_web_search_provider(make_provider())
    except Exception as exc:  # noqa: BLE001 - o plugin de tools continua útil mesmo sem o provider
        logger.warning("sabor_da_maria: provider de extração não registrado: %s", exc)
