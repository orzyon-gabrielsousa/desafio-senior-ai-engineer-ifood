"""Evidência conservadora de uma venda realizada na mensagem atual da pessoa."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .units import norm_text, strip_accents

_VERBO = re.compile(r"\b(?:vendi|vendemos|vendeu)\b")
_NUMERO = r"(?:\d+(?:[.,]\d+)?|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez)"
_UNIDADE = r"(?:porcao|porcoes|marmitas?|unidades?|pratos?)"
_QTD_VERBO = re.compile(rf"\b(?:vendi|vendemos|vendeu)\s+(?:(?:hoje|ja|ao todo|um total de)\s+)?({_NUMERO})(?![\w.,/])")
_QTD_UNIDADE = re.compile(rf"(?<![\w#/.,])({_NUMERO})\s+{_UNIDADE}\b")
_NAO_REALIZADA = re.compile(r"\b(?:se|caso|talvez|acho|supomos|suponha|hipotese|hipotetica|exemplo|"
                            r"simul\w*|projec\w*|previst\w*|planejad\w*)\b|"
                            r"\b(?:nao|nunca|jamais)\s+(?:\w+\s+){0,3}(?:vendi|vendemos|vendeu)\b|"
                            rf"\b{_NUMERO}\s+ou\s+{_NUMERO}\b")
_NAO_REGISTRAR = re.compile(r"\b(?:nao|nunca|jamais)\s+(?:\w+\s+){0,3}"
                           r"(?:registr\w*|anot\w*|salv\w*|grav\w*|lanc\w*)\b")
_PROPRIA = re.compile(r"\b(?:vendi|vendemos)\b|"
                     r"\b(?:dona maria|sabor da maria|minha loja|nossa loja|meu delivery|nosso delivery)\s+(?:ja\s+)?vendeu\b")
_EXTENSO = dict(zip(("um", "uma", "dois", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"),
                   (1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
_VALOR_DINHEIRO = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:[,.]\d{1,2})?"
_DINHEIRO = re.compile(rf"\bR\$\s*({_VALOR_DINHEIRO})(?!\d|[.,]\d)|(?<![\w#])({_VALOR_DINHEIRO})\s+reais?\b", re.I)
_PRECO_UNITARIO = re.compile(rf"(?<![\w#/.,])({_VALOR_DINHEIRO})(?!\d|[.,]\d)\s+"
                            r"(?:cada\b|por\s+(?:porcao|unidade|marmita|prato)\b)", re.I)
_DATA_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b")
_MESES = r"(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
_DIA_EXTENSO = r"(?:primeiro|um|dois|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|quinze|dezesseis|dezasseis|dezessete|dezassete|dezoito|dezenove|dezanove|vinte|trinta)"
_DATA_EXPLICITA = re.compile(r"\b(?:ontem|anteontem|semana passada|mes passado|ano passado|"
                             r"(?:segunda|terca|quarta|quinta|sexta)[ -]feira|sabado|domingo)\b|"
                             r"\b(?:na|nesta|ultima|proxima)\s+(?:segunda|terca|quarta|quinta|sexta)\b|"
                             rf"\bdia\s+(?:\d{{1,2}}|{_DIA_EXTENSO})\b|\b\d{{1,2}}\s+de\s+{_MESES}\b|"
                             r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b")
_DATA_FUTURA = re.compile(r"\b(?:amanha|(?:semana|mes|ano) que vem|proxim[oa] (?:semana|mes|ano)|"
                          r"(?:semana|mes|ano) seguinte|futur[oa])\b")


def _literal(texto: str) -> str:
    texto = re.sub(r"```[\s\S]*?```|`[^`]*`|(?m:^\s*>[^\n]*)", " ", texto)
    # Nomes entre aspas são dados; uma declaração de venda citada é só contexto.
    return re.sub(r'"([^"\n]*)"|“([^”\n]*)”|«([^»\n]*)»|\'([^\'\n]*)\'',
                  lambda m: " " if _VERBO.search(strip_accents(m[0]).lower()) else m[0].strip('"“”«»\''), texto)


def _numero(valor: Any) -> Decimal:
    if isinstance(valor, bool):
        raise ValueError("quantidade/preço não informado pela pessoa")
    normal = strip_accents(str(valor)).lower().strip()
    try:
        resultado = Decimal(_EXTENSO[normal]) if normal in _EXTENSO else Decimal(normal.replace(",", "."))
    except InvalidOperation:
        raise ValueError("quantidade/preço não informado pela pessoa") from None
    if not resultado.is_finite():
        raise ValueError("quantidade/preço não informado pela pessoa")
    return resultado


def _dinheiro(valor: str) -> Decimal:
    if "," in valor and "." in valor or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", valor):
        valor = valor.replace(".", "")
    return _numero(valor)


def conferir(texto: str, prato_id: int, nome: str, quantidade: Any, preco: Any = None, data: Any = None) -> dict[str, Any]:
    literal = _literal(texto)
    normal = strip_accents(literal).lower()
    if not _VERBO.search(normal) or _NAO_REALIZADA.search(normal):
        raise ValueError("só posso registrar uma venda já realizada, informada explicitamente agora; intenção de vender, exemplo e simulação não são vendas")
    if _NAO_REGISTRAR.search(normal):
        raise ValueError("você pediu para não registrar; a venda não será gravada")
    if _DATA_FUTURA.search(normal):
        raise ValueError("a mensagem menciona uma data futura; confirme quando a venda realmente aconteceu antes de registrar")
    if "?" in literal and not (literal.count("?") == 1 and re.search(r"(?:pode|consegue|poderia) (?:registrar|anotar|salvar)(?: isso| a venda)?\?$", normal)):
        raise ValueError("confirme a quantidade realmente vendida antes de registrar")
    # Uma declaração explícita dela basta. Um relato de terceiro exige dizer
    # que se trata da própria loja/Dona Maria, em vez de copiar qualquer venda.
    if not _PROPRIA.search(normal):
        raise ValueError("confirme que essa venda realizada pertence ao seu delivery")
    trechos = re.split(r"(?<=[.!])\s+|[;\n]", strip_accents(literal))
    candidatos = []
    for trecho in trechos:
        norm = strip_accents(trecho).lower()
        if not _VERBO.search(norm) or not _PROPRIA.search(norm):
            continue
        partes = re.split(rf"\s+e\s+(?={_NUMERO}\s+{_UNIDADE}\b)", trecho, flags=re.I)
        for parte in partes:
            ids = {int(i) for i in re.findall(r"#\s*(\d+)\b", parte)}
            nome_presente = bool(norm_text(nome) and re.search(rf"\b{re.escape(norm_text(nome))}\b", norm_text(parte)))
            if (ids and ids != {prato_id}) or (not ids and not nome_presente):
                continue
            numeros = {_numero(n) for n in _QTD_UNIDADE.findall(strip_accents(parte).lower())}
            numeros.update(_numero(n) for n in _QTD_VERBO.findall(strip_accents(parte).lower()))
            if len(numeros) == 1 and _numero(quantidade) in numeros:
                candidatos.append(parte)
    if len(candidatos) != 1:
        raise ValueError("informe o prato e a quantidade efetivamente vendida juntos; o número do prato não é a quantidade de porções")
    trecho = candidatos[0]
    precos = {_dinheiro(a or b) for a, b in _DINHEIRO.findall(trecho)}
    precos.update(_dinheiro(n) for n in _PRECO_UNITARIO.findall(strip_accents(trecho)))
    if not precos and re.search(r"R\$|\breais?\b|\bcada\b|\bpor (?:porcao|unidade|marmita|prato)\b", strip_accents(trecho), re.I):
        raise ValueError("confirme o preço realmente cobrado por porção")
    if precos and re.search(r"\b(?:total|ao todo)\b", strip_accents(trecho).lower()):
        raise ValueError("esse valor foi informado como total; confirme o preço por porção para registrar a venda")
    if preco is not None and (len(precos) != 1 or _numero(preco) not in precos):
        raise ValueError("o preço por porção precisa ser o valor que você informou agora")
    if preco is None and len(precos) > 1:
        raise ValueError("confirme qual foi o preço por porção dessa venda")
    datas = _DATA_ISO.findall(literal)
    sem_datas = _DATA_ISO.sub(" ", literal)
    if data is not None and (not isinstance(data, str) or datas != [data]):
        raise ValueError("copie a data ISO com fuso que foi informada pela pessoa; não invente uma data de venda")
    if data is None and datas:
        if len(datas) != 1:
            raise ValueError("confirme a data dessa venda")
        data = datas[0]
    if data is None and _DATA_EXPLICITA.search(strip_accents(sem_datas).lower()):
        raise ValueError("para uma venda de outro dia, informe a data exata no formulário ou em ISO com fuso horário")
    saida: dict[str, Any] = {"prato_id": prato_id, "quantidade": quantidade}
    if preco is not None or precos:
        saida["preco_unitario"] = float(_numero(preco)) if preco is not None else float(next(iter(precos)))
    if data is not None:
        saida["vendido_em"] = data
    return saida
