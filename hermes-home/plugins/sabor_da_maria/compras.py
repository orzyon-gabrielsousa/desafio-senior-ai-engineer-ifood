"""Confirmação humana e apresentação das compras que entram na despensa."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from . import costing
from .units import format_base, norm_text, parse_pantry_unit

_PEDIDO = re.compile(
    r"\b(?:comprei|compramos|ja foi comprado|ja foram comprados|"
    r"(?:adicione|coloque|lance|registre|salve|de entrada|pode adicionar|pode registrar)\b[^.!?]*\b(?:estoque|despensa)|"
    r"(?:registre|lance|confirme|confirmo)\s+(?:as?\s+)?compras?)\b"
)
_NEGACAO = re.compile(r"\bnao\s+(?:\w+\s+){0,2}(?:comprei|compramos|confirmei|confirmo|adicione|adicionei|coloque|lance|registre|salve|de entrada)\b")
_DINHEIRO = re.compile(r"\bR\$\s*(\d+(?:[.,]\d{1,2})?)(?!\d|[.,]\d)", re.I)
_MEDIDA = re.compile(r"(?<![\d.,])(?<!\w)(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|un|unidades?)\b", re.I)


def pediu_registro(texto: str) -> bool:
    normal = norm_text(texto)
    return bool(_PEDIDO.search(normal) and not _NEGACAO.search(normal) and "?" not in texto)


def confirmou_proposta(texto: str, pergunta: str) -> bool:
    aceite = norm_text(texto).strip("., ")
    pergunta_final = norm_text(pergunta.splitlines()[-1]) if pergunta.splitlines() else ""
    return (aceite in {"sim", "confirmo", "pode", "sim pode", "sim confirmo"}
            and "?" in pergunta
            and bool(re.search(r"\b(?:lancar|registrar|adicionar|dar entrada)\b.*\b(?:despensa|estoque)\b", pergunta_final)))


def medida_citada(texto: str, quantidade: float, unidade: str) -> bool:
    spec = parse_pantry_unit(unidade)
    for numero, un in _MEDIDA.findall(texto):
        medida = parse_pantry_unit("un" if un.lower().startswith("un") else un)
        if spec.base == medida.base and math.isclose(quantidade * spec.factor, float(numero.replace(",", ".")) * medida.factor):
            return True
    return False


def item_confirmado(texto: str, compra: dict[str, Any]) -> bool:
    """Nome, quantidade e preço precisam estar juntos na fala ou na linha exibida."""
    nome = re.escape(norm_text(compra["nome"]))
    texto = re.sub(r"(R\$\s*\d+(?:[.,]\d{1,2})?)\s+e\s+", r"\1\n", texto, flags=re.I)
    linhas = re.split(r"[\n;]", texto)
    for linha in linhas:
        if not re.search(rf"\b{nome}\b", norm_text(linha)):
            continue
        precos = _DINHEIRO.findall(linha)
        if (len(precos) == 1 and float(precos[0].replace(",", ".")) == compra["preco_total"]
                and medida_citada(linha, compra["quantidade"], compra["unidade"])):
            return True
    return False


def da_conferencia(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"nome": item["ingrediente"], "quantidade": item["quantidade"], "unidade": item["unidade"],
             "preco_total": item["preco_estimado"]} for item in resultado.get("compras", [])]


def mostrar(itens: list[dict[str, Any]]) -> list[str]:
    linhas = []
    for item in itens:
        spec = parse_pantry_unit(item["unidade"])
        qtd = format_base(item["quantidade"] * spec.factor, spec.base)
        linhas.append(f"{item['nome']}: {qtd}, {costing.brl(item['preco_total'])}")
    return linhas


def chave_manual(sessao: str, numero_de_falas: int, texto: str, resposta_a: str) -> str:
    dados = "\0".join((sessao, str(numero_de_falas), texto, resposta_a))
    return "chat-compra:" + hashlib.sha256(dados.encode()).hexdigest()
