"""Prepara a conferência de estoque de uma ficha para exibição."""

from typing import Any

from .fonte import ingredientes_publicados
from .montagem import nome_da_medida
from .store import Store
from .units import norm_text


def _nome(item: Any) -> str:
    return str((item.get("nome") if isinstance(item, dict) else item) or "").strip()


def _chave(nome: str) -> str:
    return norm_text(nome_da_medida(nome))


def _linhas(ingredientes: list[Any], resultado: dict[str, Any], *, omitido: bool = False) -> list[dict[str, Any]]:
    resolvidos = {norm_text(linha["ingrediente"]): linha for linha in resultado.get("itens") or []}
    linhas = []
    for item in ingredientes:
        nome = _nome(item)
        if not nome:
            continue
        resolvido = resolvidos.get(norm_text(nome), {})
        falta = resolvido.get("falta")
        original = str(item.get("ingrediente_original") or "") if isinstance(item, dict) else ""
        estado = "a_conferir"
        if resolvido.get("estoque_insuficiente"):
            estado = "faltando"
        elif falta == "nada":
            estado = "disponivel"
        linhas.append({
            "ingrediente": nome_da_medida(nome),
            "quantidade": resolvido.get("quantidade_receita") if resolvido.get("qtd_num", 0) > 0 else None,
            "estado": estado,
            "item_despensa": resolvido.get("item_despensa"),
            "uso_despensa": resolvido.get("usa_da_despensa") if resolvido.get("usa_da_despensa_base", 0) > 0 else None,
            "falta": falta if estado == "faltando" else None,
            "derivado": bool(resolvido.get("preparo_caseiro")),
            "omitido": omitido,
            "substitui": nome_da_medida(original) if original and _chave(original) != _chave(nome) else None,
        })
    return linhas


def listar(store: Store, dados: dict[str, Any], resultado: dict[str, Any],
           notas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mostra a ficha em uso e as omissões sem comprometer o estoque."""
    from .tools import _resolver_ingredientes

    ingredientes = dados.get("ingredientes") or []
    atuais = _linhas(ingredientes, resultado)
    nomes_atuais = {_chave(linha["ingrediente"]) for linha in atuais}
    trocados = [linha["substitui"] for linha in atuais if linha["substitui"]]
    fonte = dados.get("fonte_verificada") or {}
    publicados = {_chave(_nome(item)): item for item in ingredientes_publicados(fonte)}
    omitidos = {}
    for nome in [*(dados.get("ingredientes_omitidos") or []), *trocados,
                 *(nota["ingrediente"] for nota in notas if nota.get("estado") == "omitido_na_adaptacao")]:
        chave = _chave(str(nome))
        if chave and chave not in nomes_atuais:
            omitidos.setdefault(chave, {**publicados.get(chave, {}), "nome": nome_da_medida(str(nome))})
    if not omitidos:
        return atuais
    lista_omitidos = list(omitidos.values())
    return atuais + _linhas(lista_omitidos, _resolver_ingredientes(store, lista_omitidos), omitido=True)
