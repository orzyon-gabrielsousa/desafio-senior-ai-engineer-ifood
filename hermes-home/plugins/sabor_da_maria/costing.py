"""Matemática de CMV e precificação com taxa da plataforma.

Regras do desafio:

* CMV do prato = Σ (quantidade usada × custo unitário), onde o custo unitário vem
  do cruzamento das abas ``Precos`` e ``Despensa`` (preço total pago ÷ quantidade
  comprada), incluindo compras complementares.
* A taxa única da plataforma é 10% sobre o preço bruto, conforme o enunciado.
  A Dona Maria recebe 0,90 × P e o lucro é 0,90 × P − CMV.
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

TAXA_PADRAO = taxas.TAXA_PLATAFORMA
MARGENS_PADRAO = (0.30, 0.50, 0.65)
NOMES_PADRAO = ("Conservador", "Equilibrado", "Confortável")


def brl(value: float) -> str:
    """``12.5`` -> ``"R$ 12,50"``."""
    monetario = _dinheiro(Decimal(str(value)))
    sign = "-" if monetario < 0 else ""
    inteiro, centavos = divmod(int(abs(monetario) * 100), 100)
    inteiro_txt = f"{inteiro:,}".replace(",", ".")
    return f"{sign}R$ {inteiro_txt},{centavos:02d}"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


def round_up_to(value: float, step: float = 0.50) -> float:
    if step <= 0:
        return round(value, 2)
    return round(math.ceil(round(value / step, 6)) * step, 2)


def preco_minimo(cmv: float, taxa: float = TAXA_PADRAO) -> float:
    return cmv / (1.0 - TAXA_PADRAO)


def preco_minimo_centavo(cmv: float, taxa: float = TAXA_PADRAO, *, perfil: dict[str, Any] | None = None) -> float:
    """Primeiro preço, em centavos inteiros, que cobre o CMV depois da taxa."""
    return _primeiro_preco(cmv, perfil)


_CENTAVO = Decimal("0.01")


def _dinheiro(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def arredondar_moeda(valor: float) -> float:
    return float(_dinheiro(Decimal(str(valor))))


def custo_por_porcao(cmv_total: float, porcoes: float) -> float:
    """Divide os dados decimais do lote antes de converter para float.

    A divisão binária 4,32 / 3 produz 1,4400000000000002, o que subiria o
    preço mínimo de 1,60 para 1,61 ao aplicar o teto em centavos.
    """
    return float(Decimal(str(cmv_total)) / Decimal(str(porcoes)))


def _descontos(preco: Decimal, componentes: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    return tuple(_dinheiro(preco * taxa) for taxa in componentes)


def _primeiro_preco(cmv: float, perfil: dict[str, Any] | None = None, *, com_sobra: bool = False) -> float:
    # O teto aplica a desigualdade do enunciado antes do arredondamento monetário.
    # CMV 10 exige P >= 11,111…; a tela deve oferecer 11,12, nunca 11,11.
    alvo = Decimal(str(cmv)) + (_CENTAVO if com_sobra else 0)
    limite = alvo / Decimal("0.90")
    return float(limite.quantize(_CENTAVO, rounding=ROUND_CEILING))


def avaliar_preco(preco: float, cmv: float, taxa: float = TAXA_PADRAO, *, perfil: dict[str, Any] | None = None) -> dict:
    """Tudo que acontece com um preço ``P`` dado."""
    bruto = _dinheiro(Decimal(str(preco)))
    custo_exato = Decimal(str(cmv))
    # O enunciado define o repasse e o lucro antes do arredondamento. Descontar
    # uma comissão já arredondada distorce os casos de meio centavo (11,15).
    taxa_exata = bruto * Decimal("0.10")
    repasse_exato = bruto * Decimal("0.90")
    lucro_exato = repasse_exato - custo_exato
    comissao = float(_dinheiro(taxa_exata))
    liquido = float(_dinheiro(repasse_exato))
    custo = float(_dinheiro(custo_exato))
    lucro = float(_dinheiro(lucro_exato))
    return {
        "preco": float(bruto),
        "taxa_plataforma": comissao,
        "taxa_comissao": comissao,
        "taxa_pagamento": 0.0,
        "impostos": 0.0,
        "repasse_ifood": liquido,
        "recebe_liquido": liquido,
        "cmv": custo,
        "lucro": lucro,
        "margem_sobre_liquido": round(float(lucro_exato / repasse_exato), 4) if repasse_exato else None,
        "margem_sobre_preco": round(float(lucro_exato / bruto), 4) if bruto else None,
        "food_cost": round(float(custo_exato / repasse_exato), 4) if repasse_exato else None,
        "markup": round(float(bruto / custo_exato), 2) if custo_exato else None,
        "viavel": repasse_exato >= custo_exato,
        "explicacao": (
            f"Preço {brl(float(bruto))} × 10% ≈ taxa {brl(comissao)}; "
            f"0,90 × {brl(float(bruto))} ≈ recebe {brl(liquido)}; "
            f"menos CMV {brl(custo)} ≈ lucro {brl(lucro)}"
            + (f" ({pct(float(lucro_exato / repasse_exato))} do que recebe)" if repasse_exato else "")
            + ". Valores exibidos arredondados ao centavo; o cálculo usa o CMV sem arredondamento por porção."
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
    cmv = float(cmv_porcao)
    taxa = TAXA_PADRAO
    comissao, pagamento, imposto = TAXA_PADRAO, 0.0, 0.0
    deducoes = round(comissao + pagamento + imposto, 12)
    margens_lista = [float(m) for m in (margens or MARGENS_PADRAO)]
    if len(margens_lista) not in (2, 3) or any(not math.isfinite(m) or not 0 < m < 1 for m in margens_lista):
        raise ValueError("escolha 2 ou 3 margens entre 0 e 1 para comparar os cenários")
    nomes_lista = list(nomes or NOMES_PADRAO)
    minimo = preco_minimo_centavo(cmv, taxa, perfil=perfil)
    com_sobra = _primeiro_preco(cmv, perfil, com_sobra=True)
    saida = {
        "cmv_por_porcao": arredondar_moeda(cmv),
        "taxa_plataforma": round(comissao + pagamento, 12),
        "taxa_comissao": comissao,
        "taxa_pagamento": pagamento,
        "taxa_impostos": 0.0,
        "preco_minimo": minimo,
        "preco_minimo_com_sobra": com_sobra,
        "preco_minimo_explicacao": (
            f"CMV por porção de {brl(cmv)} ÷ 0,90 → mínimo {brl(minimo)} "
            f"(arredondado para cima). A plataforma fica com 10% e a Dona Maria recebe 90%. "
            f"Primeiro preço com lucro de pelo menos um centavo: {brl(com_sobra)}."
        ),
        "formula": "Preço teórico = CMV ÷ (0,90 × (1 − margem)); lucro = 0,90 × preço − CMV",
        "cenarios": [],
    }
    for i, margem in enumerate(margens_lista):
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
    return {"cmv": arredondar_moeda(cmv), "taxa": base["taxa_plataforma"], "taxa_impostos": base["taxa_impostos"],
            "preco_minimo": minimo, "preco_minimo_com_sobra": base["preco_minimo_com_sobra"], "cenarios": cenarios, "mercado": mercado,
            "formula": base["formula"]}
