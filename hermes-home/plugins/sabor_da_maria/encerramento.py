"""Entrega uma resposta já conferida pelo domínio sem outra geração da LLM.

Usa o middleware público ``llm_execution`` do Hermes. O transporte do Hermes
normaliza respostas de streaming antes deste ponto; uma resposta chat-style com
``choices`` segue o mesmo processamento, persistência e entrega ao chat.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit

from . import montagem, pesquisa

logger = logging.getLogger(__name__)

_LEITURAS_DE_SUGESTOES = frozenset({"despensa_listar", "despensa_buscar", "web_search", "web_extract",
                                  "perfil_cozinha_consultar", "analytics_ranking_pratos"})


def _prefixo_qwen(provider: str, base_url: str, model: str) -> bool:
    """Compatibilidade restrita ao Qwen3 servido pelo llama.cpp deste Compose.

    Esta versão ignora tool_choice mesmo no modo obrigatório. O prefixo de
    assistant é aceito pela API nativa e seu parser conclui a chamada de função;
    os argumentos continuam sendo gerados pelo modelo. Outros backends não
    recebem tokens específicos do template Qwen3.
    """
    try:
        url = urlsplit(base_url)
        return (provider == "custom" and model.lower() == "qwen3-8b" and url.hostname == "inference"
                and url.port == 8000 and url.path.rstrip("/") == "/v1")
    except ValueError:
        return False


def _inicio_da_chamada() -> str:
    # Não prefixar o objeto JSON: o parser incremental desta versão desconta
    # um caractere dos argumentos quando name/arguments já vieram no prefixo.
    # O modelo gera nome e JSON inteiros usando a única ferramenta no schema.
    return '<tool_call>\n'


def executar(request: dict[str, Any], next_call: Callable, *, session_id: str = "", turn_id: str = "",
             api_mode: str = "", model: str = "", provider: str = "", base_url: str = "", **_: Any) -> Any:
    """O modelo continua conduzindo até existir proposta viável, análise ou registro.

    Nenhuma ferramenta é suprimida antes dessa validação. Uma ficha bloqueada
    ainda pode precisar de busca de preço, correção de medida ou outra proposta.
    Outros formatos de API preservam o transporte original.
    """
    texto = montagem.resposta_preparada(session_id, turn_id) if api_mode == "chat_completions" else None
    if texto is None:
        if api_mode == "chat_completions" and pesquisa.somente_sugestoes(session_id):
            # Conhecer opções é uma leitura. A seleção vem numa mensagem
            # seguinte e então o hook abre novamente as ferramentas de escrita.
            request = {**request, "tools": [t for t in request.get("tools") or []
                                            if isinstance(t, dict) and (t.get("function") or {}).get("name") in _LEITURAS_DE_SUGESTOES]}
            escolha = request.get("tool_choice")
            if isinstance(escolha, dict) and (escolha.get("function") or {}).get("name") not in _LEITURAS_DE_SUGESTOES:
                request = {**request, "tool_choice": "auto"}
        ferramenta = pesquisa.ferramenta_da_conducao(session_id) if api_mode == "chat_completions" else None
        disponivel = any(isinstance(t, dict) and (t.get("function") or {}).get("name") == ferramenta
                         for t in request.get("tools") or []) if ferramenta else False
        logger.info("Condução LLM: sessão=%s turno=%s modo=%s próxima=%s ferramentas=%s disponível=%s",
                    session_id, turn_id, api_mode, ferramenta, len(request.get("tools") or []), disponivel)
        if disponivel:
            alvo = pesquisa.alvo_escolhido(session_id) or {}
            if ferramenta == "receita_conferir_ingredientes" and alvo.get("adaptacao_delegada"):
                selecionada = next(t for t in request["tools"] if (t.get("function") or {}).get("name") == ferramenta)
                funcao = selecionada["function"]
                parametros = funcao.get("parameters") or {}
                adaptada = {**selecionada, "function": {**funcao, "parameters": {**parametros,
                    "required": ["prato_id", "porcoes", "ingredientes", "ingredientes_omitidos", "adaptacao", "modo_preparo", "tempo_preparo_min"]}}}
                request = {**request, "tools": [adaptada if t is selecionada else t for t in request["tools"]]}
            # A pessoa já delegou a montagem. O modelo escolhe os argumentos da
            # busca/leitura; não pode encerrar pedindo licença para ler o estoque.
            if _prefixo_qwen(provider, base_url, model or str(request.get("model") or "")):
                selecionada = next(t for t in request["tools"] if (t.get("function") or {}).get("name") == ferramenta)
                request = {**request, "tools": [selecionada], "tool_choice": "required",
                           "messages": [*(request.get("messages") or []),
                                        {"role": "assistant", "content": _inicio_da_chamada()}]}
            else:
                request = {**request, "tool_choice": {"type": "function", "function": {"name": ferramenta}}}
        return next_call(request)
    logger.info("Resposta conferida entregue sem nova inferência: sessão=%s turno=%s", session_id, turn_id)
    return SimpleNamespace(
        id=f"sabor-conferido-{turn_id}", object="chat.completion", created=int(time.time()),
        model=model or str(request.get("model") or "sabor-da-maria"),
        choices=[SimpleNamespace(index=0, finish_reason="stop", logprobs=None,
                                 message=SimpleNamespace(role="assistant", content=texto, tool_calls=None,
                                                         function_call=None, refusal=None,
                                                         reasoning=None, reasoning_content=None, reasoning_details=None))],
        # Não atribuir tokens ou cobrança a uma chamada que não foi feita.
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )
