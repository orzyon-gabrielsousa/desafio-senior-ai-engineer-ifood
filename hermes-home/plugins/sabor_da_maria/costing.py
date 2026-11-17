"""Matemática de CMV e precificação com taxa da plataforma.

Regras do desafio:

* CMV do prato = Σ (quantidade usada × custo unitário), onde o custo unitário vem
  do cruzamento das abas ``Precos`` e ``Despensa`` (preço total pago ÷ quantidade
  comprada), incluindo compras complementares.
* Comissão, pagamento pela plataforma e alíquota efetiva estimada incidem sobre o preço bruto.
  O preço de equilíbrio cobre o CMV com o valor que sobra depois dos descontos.
* O agente propõe 2–3 cenários e deixa a Dona Maria decidir.

Convenção de "margem" aqui: lucro ÷ valor recebido (líquido da taxa). Com margem
``m``: ``P = CMV / ((1 − descontos) · (1 − m))``. A saída também traz food cost (CMV ÷ líquido)
e markup (P ÷ CMV) porque são os números que donos de restaurante conhecem.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Iterable, Optional

from . import taxas

TAXA_PADRAO = 0.10
MARGENS_PADRAO = (0.30, 0.50, 0.65)
NOMES_PADRAO = ("Conservador", "Equilibrado", "Confortável")


def brl(value: float) -> str:
    """``12.5`` -> ``"R$ 12,50"``."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    inteiro, centavos = divmod(round(value * 100), 100)
    inteiro_txt = f"{inteiro:,}".replace(",", ".")
    return f"{sign}R$ {inteiro_txt},{centavos:02d}"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


def round_up_to(value: float, step: float = 0.50) -> float:
    if step <= 0:
        return round(value, 2)
    return round(math.ceil(round(value / step, 6)) * step, 2)


def preco_minimo(cmv: float, taxa: float = TAXA_PADRAO) -> float:
    return cmv / (1.0 - taxa)


def preco_minimo_centavo(cmv: float, taxa: float = TAXA_PADRAO, *, perfil: dict[str, Any] | None = None) -> float:
    """Primeiro preço, em centavos inteiros, que cobre o CMV depois da taxa."""
    if perfil is not None:
        return _primeiro_preco(cmv, perfil)
    return math.ceil(round(preco_minimo(cmv, taxa) * 100, 9)) / 100


_CENTAVO = Decimal("0.01")
_MEIO_CENTAVO = Decimal("0.005")


def _dinheiro(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _descontos(preco: Decimal, componentes: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    return tuple(_dinheiro(preco * taxa) for taxa in componentes)


def _primeiro_preco(cmv: float, perfil: dict[str, Any], *, com_sobra: bool = False) -> float:
    componentes = tuple(Decimal(str(taxa)) for taxa in taxas.componentes(perfil))
    alvo = _dinheiro(Decimal(str(cmv))) + (_CENTAVO if com_sobra else 0)
    # Cada desconto arredondado pode reduzir a dedução teórica em até meio centavo.
    # Começa nesse limite para não pular um centavo que cubra o custo.
    folga = _MEIO_CENTAVO * sum(taxa > 0 for taxa in componentes)
    limite = (alvo - folga) / (1 - sum(componentes))
    preco = max(_CENTAVO if com_sobra else Decimal(0), limite.quantize(_CENTAVO, rounding=ROUND_CEILING))
    while preco - sum(_descontos(preco, componentes)) < alvo:
        preco += _CENTAVO
    return float(preco)


def avaliar_preco(preco: float, cmv: float, taxa: float = TAXA_PADRAO, *, perfil: dict[str, Any] | None = None) -> dict:
    """Tudo que acontece com um preço ``P`` dado."""
    taxas_ativas = taxas.componentes(perfil) if perfil is not None else (taxa, 0.0, 0.0)
    bruto = _dinheiro(Decimal(str(preco)))
    comissao, pagamento, imposto = _descontos(bruto, tuple(Decimal(str(t)) for t in taxas_ativas))
    ifood = comissao + pagamento
    liquido = float(bruto - ifood - imposto)
    custo = float(_dinheiro(Decimal(str(cmv))))
    lucro = float(bruto - ifood - imposto - Decimal(str(custo)))
    return {
        "preco": float(bruto),
        "taxa_plataforma": float(ifood),
        "taxa_comissao": float(comissao),
        "taxa_pagamento": float(pagamento),
        "impostos": float(imposto) if perfil is None or perfil["imposto"] is not None else None,
        "repasse_ifood": float(bruto - ifood),
        "recebe_liquido": liquido,
        "cmv": custo,
        "lucro": lucro,
        "margem_sobre_liquido": round(lucro / liquido, 4) if liquido else None,
        "margem_sobre_preco": round(lucro / preco, 4) if preco else None,
        "food_cost": round(cmv / liquido, 4) if liquido else None,
        "markup": round(preco / cmv, 2) if cmv else None,
        "viavel": lucro >= -1e-9,
        "explicacao": (
            f"Preço {brl(preco)} → taxas iFood {brl(float(ifood))} → "
            + ("impostos pendentes" if perfil is not None and perfil["imposto"] is None else f"impostos {brl(float(imposto))}")
            + f" → líquido estimado {brl(liquido)} → menos CMV {brl(custo)} = sobra {brl(lucro)}"
            + (f" ({pct(lucro / liquido)} do que recebe)" if liquido else "")
        ),
    }


def cenarios_preco(
    cmv_porcao: float,
    taxa: float = TAXA_PADRAO,
    margens: Optional[Iterable[float]] = None,
    nomes: Optional[Iterable[str]] = None,
    arredondar_para: float = 0.50,
    *,
    perfil: dict[str, Any] | None = None,
) -> dict:
    cmv = float(_dinheiro(Decimal(str(cmv_porcao))))
    taxa = float(taxa)
    comissao, pagamento, imposto = taxas.componentes(perfil) if perfil is not None else (taxa, 0.0, 0.0)
    deducoes = round(comissao + pagamento + imposto, 12)
    margens_lista = [float(m) for m in (margens or MARGENS_PADRAO)]
    nomes_lista = list(nomes or NOMES_PADRAO)
    minimo = preco_minimo_centavo(cmv, taxa, perfil=perfil)
    com_sobra = _primeiro_preco(cmv, perfil, com_sobra=True) if perfil is not None else round_up_to((cmv + 0.005) / (1 - taxa), 0.01)
    saida = {
        "cmv_por_porcao": round(cmv, 2),
        "taxa_plataforma": round(comissao + pagamento, 12),
        "taxa_comissao": comissao,
        "taxa_pagamento": pagamento,
        "taxa_impostos": None if perfil is not None and perfil["imposto"] is None else imposto,
        "preco_minimo": minimo,
        "preco_minimo_com_sobra": com_sobra,
        "preco_minimo_explicacao": (
            f"CMV por porção de {brl(cmv)} e descontos considerados de {pct(deducoes)}. "
            f"Equilíbrio estimado {brl(minimo)}. Primeiro preço com sobra de pelo menos um centavo {brl(com_sobra)}."
        ),
        "formula": "Preço teórico = CMV ÷ ((1 − descontos) × (1 − margem)); sobra = preço − descontos em reais − CMV",
        "cenarios": [],
    }
    for i, margem in enumerate(margens_lista):
        if margem >= 1:
            continue
        fator = (1.0 - deducoes) * (1.0 - margem)
        preco_exato = cmv / fator
        sugerido = round_up_to(preco_exato, arredondar_para)
        nome = nomes_lista[i] if i < len(nomes_lista) else f"Cenário {i + 1}"
        saida["cenarios"].append({
            "nome": nome,
            "margem_alvo": margem,
            "preco_exato": round(preco_exato, 2),
            "preco_sugerido": sugerido,
            "calculo": (
                f"P = {brl(cmv)} ÷ {fator:.4f} = {brl(preco_exato)} "
                f"→ arredondado para {brl(sugerido)}"
            ),
            "com_preco_sugerido": avaliar_preco(sugerido, cmv, taxa, perfil=perfil),
        })
    return saida


def simular_precos(cmv_porcao: float, precos: Iterable[float], taxa: float = TAXA_PADRAO,
                  *, perfil: dict[str, Any] | None = None) -> list[dict]:
    return [avaliar_preco(float(p), float(cmv_porcao), taxa, perfil=perfil) for p in precos]


def quanto_cobrar(cmv: float, preco_mercado: Optional[float] = None, taxa: float = TAXA_PADRAO,
                  margens: Optional[Iterable[float]] = None, *, perfil: dict[str, Any] | None = None) -> dict:
    """Quanto cobrar para o delivery dar lucro depois da taxa: o que o Analytics e a calculadora da tela mostram.

    * ``preco_minimo``: o menor preço que cobre o CMV depois dos descontos considerados.
    * ``cenarios``: o preço para sobrar cada margem do que ela recebe (mesma conta e mesmo arredondamento de
      R$ 0,50 do ``preco_sugerir``, então a tela e a consultora dão o mesmo número).
    * ``mercado``: o que acontece se ela cobrar o preço médio que o mercado paga (``None`` sem preço).
    """
    cmv = float(cmv)
    if not math.isfinite(cmv) or cmv <= 0:
        raise ValueError("informe o custo do prato (CMV), maior que zero")
    base = cenarios_preco(cmv, taxa, margens, perfil=perfil)
    minimo = base["preco_minimo"]
    cenarios = [{"nome": c["nome"], "margem": c["margem_alvo"], "preco": c["preco_sugerido"],
                 "recebe": c["com_preco_sugerido"]["recebe_liquido"], "lucro": c["com_preco_sugerido"]["lucro"]}
                for c in base["cenarios"]]
    mercado = avaliar_preco(float(preco_mercado), cmv, taxa, perfil=perfil) if preco_mercado else None
    return {"cmv": round(cmv, 2), "taxa": base["taxa_plataforma"], "taxa_impostos": base["taxa_impostos"],
            "preco_minimo": minimo, "preco_minimo_com_sobra": base["preco_minimo_com_sobra"], "cenarios": cenarios, "mercado": mercado,
            "formula": base["formula"]}
