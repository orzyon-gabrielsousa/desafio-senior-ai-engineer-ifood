"""Procura uma receita parecida que dê para fazer com o estoque disponível."""

from __future__ import annotations

import math
from typing import Any, Callable

from .matching import canonical_query, tokens
from .store import Store
from .units import norm_text

_TERMOS_GENERICOS = {"almoco", "marmita", "marmitas", "pratico", "caseiro", "facil", "especial", "receita", "delicioso", "simples"}
_TEMPEROS_COMUNS = {"agua", "alho", "cebola", "sal", "oleo de soja", "azeite de oliva extra virgem"}


def _itens_da_receita(receita: dict[str, Any]) -> list[Any]:
    return (receita.get("dados") or {}).get("ingredientes") or []


def _ingredientes_principais(receita: dict[str, Any]) -> set[str]:
    ingredientes = _itens_da_receita(receita)
    return {canonical_query(ing["nome"]) for ing in ingredientes if isinstance(ing, dict) and isinstance(ing.get("nome"), str)} - _TEMPEROS_COMUNS


def _sobreposicao(a: set[str], b: set[str]) -> tuple[int, float]:
    comum = len(a & b)
    return comum, comum / len(a | b) if comum else 0.0


def _afinidade(alvo: dict[str, Any], candidata: dict[str, Any]) -> float:
    nome_alvo, nome_candidata = alvo["nome"], candidata["nome"]
    if norm_text(nome_alvo) == norm_text(nome_candidata):
        return 0.0
    comuns, afinidade_nome = _sobreposicao(tokens(nome_alvo) - _TERMOS_GENERICOS, tokens(nome_candidata) - _TERMOS_GENERICOS)
    itens_comuns, afinidade_itens = _sobreposicao(_ingredientes_principais(alvo), _ingredientes_principais(candidata))
    if (comuns >= 2 and afinidade_nome >= 0.35) or (itens_comuns >= 2 and afinidade_itens >= 0.5):
        return afinidade_nome + afinidade_itens
    return 0.0


def _ficha_quantificada(receita: dict[str, Any], ingredientes: list[Any]) -> bool:
    """A conferência também aceita nomes avulsos; a alternativa requer receita com rendimento e quantidades."""
    try:
        porcoes = float(receita.get("porcoes"))
        if not math.isfinite(porcoes) or porcoes <= 0 or not ingredientes:
            return False
        for ing in ingredientes:
            if not isinstance(ing, dict) or not str(ing.get("nome") or "").strip() or not str(ing.get("unidade") or "").strip():
                return False
            qtd = float(str(ing.get("quantidade")).replace(",", "."))
            if not math.isfinite(qtd) or qtd <= 0:
                return False
    except (ValueError, TypeError):
        return False
    return True


def alternativa_no_estoque(store: Store, receita_ou_nome: dict[str, Any] | str,
                           avaliar: Callable[[Store, list[Any], dict[str, Any]], dict[str, Any]]) -> dict[str, Any] | None:
    """Revalida receitas já conferidas com o perfil e a despensa atuais, sem registrar prato ou compra."""
    alvo = receita_ou_nome if isinstance(receita_ou_nome, dict) else {"nome": receita_ou_nome}
    if not alvo.get("nome"):
        return None
    candidatas = []
    for resumo in store.receitas():
        if resumo["id"] == alvo.get("id") or resumo["status"] != "conferida":
            continue
        receita = store.obter_receita(resumo["id"])
        if receita is not None:
            afinidade = _afinidade(alvo, receita)
            if afinidade:
                candidatas.append((afinidade, receita))
    candidatas.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)

    for _, receita in candidatas:
        ingredientes = _itens_da_receita(receita)
        if not _ficha_quantificada(receita, ingredientes):
            continue
        try:
            atual = avaliar(store, ingredientes, receita)
        except (ValueError, KeyError):
            continue  # Receita antiga com unidade ou dados que a conferência atual não aceita.
        if atual["veredito"] != "viavel" or atual.get("avisos_operacionais"):
            continue
        if (atual.get("cozinha", {}).get("status") == "sem_ficha"
                and not (atual.get("requisitos", {}).get("equipamentos_necessarios")
                         and atual.get("requisitos", {}).get("tecnicas_necessarias"))):
            continue
        res = atual["resultado"]
        linhas = res["itens"]
        if res["compras"] or res["pendencias"] or res["estimativas"] or len(linhas) != len(ingredientes):
            continue
        if any(linha.get("item_id") is None or linha.get("falta") != "nada" for linha in linhas):
            continue
        # A conferência mede cada linha sozinha; linhas repetidas não comprovam o total disponível.
        if len({linha["item_id"] for linha in linhas}) != len(linhas):
            continue
        return {"receita_id": receita["id"], "nome": receita["nome"], "porcoes": receita["porcoes"],
                "fonte_url": receita["fonte_url"], "cmv_total": res["cmv_total"], "compras_estimadas": 0.0,
                "ingredientes": [{"nome": linha["item_despensa"], "quantidade": linha["necessario"]} for linha in linhas]}
    return None
