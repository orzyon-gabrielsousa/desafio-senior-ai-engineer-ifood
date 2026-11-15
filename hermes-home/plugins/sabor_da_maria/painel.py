"""Painel da tela para a consultora: carrinho, saldo, itens salvos, perfil do negócio, a cozinha dela e mais vendidos, em todo turno.

Pedir que o modelo lembre de chamar ``carrinho_consultar`` antes de sugerir não é confiável (um 8B pula
o passo), e o que a Dona Maria mexe na tela não passa por conversa nenhuma. Então o plugin injeta este
resumo curto na mensagem do turno pelo hook ``pre_llm_call`` (o Hermes junta o contexto dos plugins à
mensagem do usuário, nunca ao prompt de sistema). A consultora sempre sabe o saldo, o que já foi
escolhido (os pratos abertos, com ingredientes e valores), o que ela salvou, para que tipo de negócio e
em que lugar sugerir e quais são os pratos mais vendidos ali (base de exemplo).

A **cozinha dela** (equipamentos, utensílios, técnicas e rotina, salvos no SQLite) também vai em todo turno, porque é
ela que decide se um prato pode ser aceito. Enquanto o perfil está incompleto o painel manda perguntar, com o texto das
perguntas já pronto (a lição do carrinho: o 8B só repete com fidelidade o que chega pronto num campo), e na primeira
mensagem da conversa manda perguntar já na primeira resposta. Hermes avisa o hook com ``is_first_turn``.

O bloco começa com ``MARCA_PAINEL`` para a trava de evidência (falas.py) ignorá-lo: são dados da tela,
não palavras dela.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import analytics, costing, cozinha, negocio
from .falas import MARCA_PAINEL
from .store import Store
from .tools import _pendencias_perfil

logger = logging.getLogger(__name__)

MAX_ITENS = 12  # linhas do carrinho (e ingredientes de cada prato) citadas; o resto vira "e mais N"
MAX_SALVOS = 8


def _qtd(i: dict[str, Any]) -> str:
    return f"{i['quantidade']:g}" + (f" {i['unidade']}" if i.get("unidade") else "")


def _item(i: dict[str, Any]) -> str:
    extras = (", já tem" if i.get("ja_tem") else "") + (", preço estimado" if i.get("estimado") else "")
    return f"{i['nome']} ({_qtd(i)}, {costing.brl(i['preco_total'])}{extras})"


def _mais(lista: list[Any], limite: int) -> str:
    return f"; e mais {len(lista) - limite} itens" if len(lista) > limite else ""


def _carrinho(store: Store) -> str:
    pratos = store.carrinho_pratos()
    avulsos = [i for i in store.carrinho_itens() if not i["prato"]]
    partes = []
    for g in pratos:
        ings = "; ".join(_item(i) for i in g["ingredientes"][:MAX_ITENS]) + _mais(g["ingredientes"], MAX_ITENS)
        partes.append(f"PRATO {g['prato']} {costing.brl(g['valor_do_prato'])} [{ings}]")
    partes += [_item(i) for i in avulsos[:MAX_ITENS]]
    return " | ".join(partes) + _mais(avulsos, MAX_ITENS) if partes else "vazio"


def _salvos(store: Store) -> str:
    s = store.salvos()
    if not s["pratos"] and not s["ingredientes"]:
        return "nada ainda"
    pratos = "; ".join(f"{p['nome']} ({costing.brl(p['preco_total'])})" for p in s["pratos"][:MAX_SALVOS])
    ingredientes = "; ".join(f"{i['nome']} ({costing.brl(i['preco_total'])})" for i in s["ingredientes"][:MAX_SALVOS])
    return f"pratos: {pratos or 'nenhum'} | ingredientes: {ingredientes or 'nenhum'}"


def _cozinha(store: Store, primeira_mensagem: bool) -> list[str]:
    """A cozinha dela e, enquanto faltar resposta, a ordem de perguntar (com as perguntas prontas)."""
    perfil = store.perfil()
    pend = _pendencias_perfil(perfil)
    linhas = [f"Cozinha dela (menu Minha cozinha; o que ela marca lá ou conta aqui fica salvo): {cozinha.texto_do_perfil(perfil)}"]
    if not pend:
        linhas.append("Cozinha completa: não pergunte de novo o que está salvo. Ao sugerir ou montar um prato, confira com o que ela tem e sabe; "
                      "se um prato ou ingrediente não cabe na cozinha dela, avise e diga por quê (o campo alerta_da_cozinha das tools já traz a frase).")
        return linhas
    if primeira_mensagem:
        linhas.append("COZINHA INCOMPLETA E ESTA É A PRIMEIRA MENSAGEM DA CONVERSA: cumprimente, diga em uma frase o que você faz, responda o que ela pediu "
                      "(se pediu algo) e faça JÁ NESTA RESPOSTA as perguntas abaixo, em texto, copiando como estão (já só têm o que falta). "
                      "Se a mensagem dela já responde alguma, salve com perfil_cozinha_salvar e pergunte só o resto. Nenhum prato é aceito antes disso.")
    else:
        linhas.append(f"COZINHA INCOMPLETA ({len(pend)} perguntas sem resposta): antes de aceitar qualquer prato, releia a última mensagem dela: "
                      "se ela já falou de equipamentos, técnicas ou rotina, salve com perfil_cozinha_salvar (técnicas em 'tecnicas': nome → sim, nao ou "
                      "aprendendo) e só pergunte o que sobrar, copiando as perguntas abaixo sem repetir o que ela já respondeu.")
    linhas.append("PERGUNTAS DA COZINHA (copie):\n" + cozinha.perguntas_da_cozinha(pend))
    return linhas


def texto_do_painel(store: Optional[Store] = None, primeira_mensagem: bool = False) -> str:
    store = store or Store()
    resumo = store.carrinho_resumo()
    partes = [f"orçamento {costing.brl(resumo['inicial'])}"]
    if resumo["comprometido"]:
        partes.append(f"já comprometido em pratos {costing.brl(resumo['comprometido'])}")
    partes.append(f"no carrinho {costing.brl(resumo['carrinho'])}")
    partes.append(f"saldo {costing.brl(resumo['saldo'])}")
    neg = store.negocio()
    linhas = [
        f"{MARCA_PAINEL} — dados atuais, não são fala dela]",
        "Saldo: " + " · ".join(partes) + ".",
        f"Carrinho: {_carrinho(store)}.",
        f"Salvos (Meus pratos e ingredientes): {_salvos(store)}.",
        f"Negócio: {negocio.descrever(neg)}",
    ]
    linhas += _cozinha(store, primeira_mensagem)
    topo = analytics.topo_para_painel(neg, perfil=store.perfil())
    if topo:
        linhas.append(topo)
    linhas += [
        "Use isto ao sugerir pratos e ingredientes e ao falar do carrinho. Se ela pedir um item que já está no carrinho, avise. "
        "Prato é sempre aberto: ingredientes com o valor de cada um. Ao falar de saldo, use os números daqui ou do campo diga_a_dona_maria das tools.",
        "[fim do painel]",
    ]
    return "\n".join(linhas)


def ao_iniciar_turno(is_first_turn: bool = False, **_: Any) -> Optional[dict[str, str]]:
    """Hook ``pre_llm_call``: devolve o painel como contexto do turno. Nunca derruba o turno."""
    try:
        return {"context": texto_do_painel(primeira_mensagem=bool(is_first_turn))}
    except Exception:  # noqa: BLE001 - sem o painel a consultora ainda tem as tools
        logger.exception("painel: não consegui montar o resumo do carrinho")
        return None
