"""A pesquisa de receitas (item 2.1 do desafio) na ordem certa, para o modelo pequeno não pular etapa.

O Hermes já traz as ferramentas (``web_search`` com SearXNG e ``web_extract`` com o provider ``receitas-local`` deste plugin). O que o Qwen3-8B
faz sozinho, visto em teste real: uma busca e a resposta em cima dos trechos dos resultados, sem ler a página nem conferir a despensa; ou
"vou pesquisar na web, um momento" e o fim do turno, sem chamar nada; ou, perguntado de forma aberta ("o que eu consigo fazer?"), nem busca.
Dois empurrões, sem tocar no core:

* ``roteiro_do_turno``: quando a mensagem dela pede receita ou ideia de prato, o painel do turno traz o roteiro em ordem, e o turno fica marcado
  (``marcar_turno``, por sessão);
* ``depois_da_ferramenta`` (hook ``transform_tool_result``): nesse turno, o resultado da despensa, da busca na internet e da leitura da página
  leva o próximo passo escrito, no mesmo campo ``proximo_passo`` que as tools do plugin usam. Quem conduz é a ferramenta que acabou de
  responder, que é o que um modelo pequeno mais respeita.

Quem decide o que cozinhar continua sendo ela: o roteiro termina sempre na pergunta "você gosta de cozinhar isso? vê algum impedimento?".
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from typing import Any, Optional

from .units import norm_text

logger = logging.getLogger(__name__)

MAX_SESSOES = 16

# Pede receita ou ideia do que cozinhar. "despensa" e "cardápio" sozinhos não entram: "quanto paguei?" e "mostra o cardápio" não são pesquisa.
PEDE_RECEITA = re.compile(
    r"\bmontar (?:o |um |meu )?cardapio|\bo que (?:eu )?(?:posso|consigo|da pra|vale a pena) (?:fazer|cozinhar)|"
    r"\bgosto de (?:fazer|cozinhar)|\bideias? de (?:prato|receita|comida|marmita)|\bmais (?:vendidos?|pedidos?)\b|\bvendem? bem\b|"
    r"\b(?:pesquis\w*|busc\w*|procur\w*|sug\w*|quer[oa]|encontr\w*|mostr\w*|mont[ae]|montar)\b.{0,85}\b(?:receitas?|sugestoes de pratos?)\b|"
    r"\bcomo (?:fazer|preparar|cozinhar)\b")
_BUSCA_DE_PRECO = re.compile(r"^(?:preco|quanto custa|cotacao|valor)\b")

ROTEIRO = (
    "PESQUISA DE RECEITAS (ela pediu receita ou ideia de prato): siga esta ordem, uma ferramenta por vez, sem pular etapa e SEM anunciar o que vai fazer "
    "('vou pesquisar...'): chame a ferramenta. "
    "(1) despensa_listar: o que ela tem em quantidade. "
    "(2) web_search em português com 2 ou 3 ingredientes que ela TEM e o estilo que ela pediu, como 'receita fácil de frango com batata para marmita'. "
    "Use equipamentos para filtrar se o preparo é possível. Use ingredientes para montar a consulta. "
    "Use as fontes preferidas como ponto de partida. Para mais vendidos, use os recortes de pedidos publicados; tradição é pista para testar, não ranking. "
    "(3) web_extract nas 2 melhores páginas (as que trazem a receita completa), as duas na mesma chamada. "
    "(4) despensa_buscar com os ingredientes das receitas lidas, numa chamada só. "
    "(5) responda em TEXTO, sem usar o carrinho. Use títulos numerados '### 1. Nome do prato', uma linha 'Fonte: [título](URL)' em cada opção, "
    "e liste todos os ingredientes e o que precisa comprar ou conferir. Só diga 'tem tudo' quando a despensa confirmar todos os ingredientes e as quantidades. "
    "Mantenha o corte da fonte. Peito de frango e coxinha da asa exigem quantidades e preparo diferentes. Pergunte: 'Qual opção você quer guardar? Pode escolher mais de uma. "
    "Você gosta de cozinhar isso? Vê algum impedimento?' "
    "O carrinho, a conferência dos ingredientes e o preço entram depois da escolha: "
    "não dê preço de ingrediente que falta (nem 'estimativa'), porque o preço vem depois, pesquisado ou dito por ela. "
    "Só afirme o que as ferramentas devolveram (receita que você não leu na página não se descreve) e não cite nomes de ferramentas para ela."
)

PASSO_APOS_DESPENSA = (
    "Próximo passo: chame web_search AGORA, sem avisar antes e sem responder ainda, com 2 ou 3 ingredientes que ela tem em quantidade e o estilo que ela pediu, "
    "como 'receita fácil de frango com batata para marmita'. Se ajudar, inclua a região ou refine por um site do guia de fontes."
)

PASSO_APOS_BUSCA = (
    "Agora leia as 2 melhores páginas com web_extract(urls=[...]), as duas na mesma chamada e de preferência as que trazem a receita completa "
    "(ingredientes e modo de preparo). Depois confira os ingredientes com despensa_buscar e só então apresente as candidatas em texto, sem usar o carrinho, "
    "perguntando de cada uma: 'Você gosta de cozinhar isso? Vê algum impedimento?'. Não descreva receita só pelo trecho do resultado da busca."
)

BUSCA_SEM_RESULTADO = ("Nenhuma página foi encontrada. Tente outra consulta ou outro domínio do guia de fontes. "
                       "Se não conseguir ler uma receita real, informe a limitação em vez de inventar fonte ou dizer que leu.")

PASSO_APOS_LEITURA = (
    "Agora chame despensa_buscar com os ingredientes das receitas lidas (uma chamada, todos de uma vez) e responda em TEXTO, sem usar o carrinho: para cada "
    "candidata, nome, fonte (link), o que ela já tem e o que falta comprar (sem preço: o preço vem depois, pesquisado ou dito por ela). "
    "Pergunte de cada uma: 'Você gosta de cozinhar isso? Vê algum impedimento?'. Fale só do que está na página e do que a despensa confirmou."
)

PASSO_APOS_CONFERENCIA = (
    "Agora apresente as candidatas, sem usar o carrinho. Use '### 1. Nome do prato', 'Fonte: [título](URL)' e numere as opções na ordem. "
    "Já tem (nomes exatos dos itens confirmados); Precisa conferir ou comprar (nomes, sem valores em R$). Inclua todos os ingredientes lidos. "
    "Se o corte da receita não aparecer no estoque, mostre a falta e ofereça uma adaptação separada para ela decidir. "
    "Pergunte quais ela quer guardar e 'Você gosta de cozinhar isso? Vê algum impedimento?'. "
    "Se ela pediu opções diferentes, busque outro prato em vez de apresentar duas páginas da mesma receita. "
    "Deixe as cotações para depois da escolha. Confira a versão final e peça o aceite antes de calcular preço."
)

_PESQUISANDO: "OrderedDict[str, bool]" = OrderedDict()
_LOCK = threading.Lock()


def pede_receita(mensagem: Any) -> bool:
    return isinstance(mensagem, str) and bool(PEDE_RECEITA.search(norm_text(mensagem)))


def roteiro_do_turno(mensagem: Any) -> Optional[str]:
    """O roteiro da pesquisa, quando a mensagem dela pede receita ou ideia; senão nada (o painel não vira lista de ordens)."""
    return ROTEIRO if pede_receita(mensagem) else None


def marcar_turno(sessao: str, mensagem: Any) -> None:
    """Guarda se o turno desta sessão é de pesquisa de receitas, para as ferramentas do turno saberem (o hook delas não vê a mensagem)."""
    if not sessao:
        return
    with _LOCK:
        _PESQUISANDO[sessao] = pede_receita(mensagem)
        _PESQUISANDO.move_to_end(sessao)
        while len(_PESQUISANDO) > MAX_SESSOES:
            _PESQUISANDO.popitem(last=False)


def pesquisando(sessao: str) -> bool:
    with _LOCK:
        return bool(sessao) and _PESQUISANDO.get(sessao, False)


def esquecer_tudo() -> None:
    with _LOCK:
        _PESQUISANDO.clear()


def _busca_de_receita(args: Any) -> bool:
    consulta = args.get("query") if isinstance(args, dict) else None
    return isinstance(consulta, str) and "receita" in norm_text(consulta)


def _passo(ferramenta: str, args: Any, dados: dict[str, Any], sessao: str) -> Optional[str]:
    if dados.get("error") or dados.get("success") is False:
        return None
    em_pesquisa = pesquisando(sessao)
    if ferramenta == "despensa_listar" and em_pesquisa:
        return PASSO_APOS_DESPENSA
    if ferramenta == "despensa_buscar" and em_pesquisa:
        return PASSO_APOS_CONFERENCIA
    if ferramenta == "web_search" and (em_pesquisa or _busca_de_receita(args)):
        consulta = args.get("query", "") if isinstance(args, dict) else ""
        if _BUSCA_DE_PRECO.search(norm_text(consulta)):
            return None
        return PASSO_APOS_BUSCA if (dados.get("data") or {}).get("web") else BUSCA_SEM_RESULTADO
    if ferramenta == "web_extract" and any("## Ingredientes" in str(p.get("content") or "") for p in dados.get("results") or [] if isinstance(p, dict)):
        return PASSO_APOS_LEITURA
    return None


def depois_da_ferramenta(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_: Any) -> Optional[str]:
    """Hook ``transform_tool_result``: devolve o resultado com ``proximo_passo``, ou ``None`` para deixar como está. Nunca derruba o turno."""
    if tool_name not in ("despensa_listar", "despensa_buscar", "web_search", "web_extract") or not isinstance(result, str):
        return None
    try:
        try:
            dados = json.loads(result)
        except ValueError:  # resposta em texto puro (erro de rede, por exemplo): não é o formato que este hook conhece
            return None
        passo = _passo(tool_name, args, dados, session_id) if isinstance(dados, dict) else None
        return json.dumps({**dados, "proximo_passo": passo}, ensure_ascii=False) if passo else None
    except Exception:  # noqa: BLE001 - sem o empurrão o modelo ainda tem o resultado original
        logger.exception("pesquisa: não consegui acrescentar o próximo passo a %s", tool_name)
        return None
