"""Matemática de CMV e precificação com taxa da plataforma.

Regras do desafio:

* CMV do prato = Σ (quantidade usada × custo unitário), onde o custo unitário vem
  do cruzamento das abas ``Precos`` e ``Despensa`` (preço total pago ÷ quantidade
  comprada), incluindo compras complementares.
* Taxa de 10% sobre a venda: com preço ``P`` a Dona Maria recebe ``0,90·P``.
  Preço mínimo para não perder dinheiro: ``P ≥ CMV / 0,90``. Lucro: ``0,90·P − CMV``.
* O agente propõe 2–3 cenários e deixa a Dona Maria decidir.

Convenção de "margem" aqui: lucro ÷ valor recebido (líquido da taxa). Com margem
``m``: ``P = CMV / (0,90 · (1 − m))``. A saída também traz food cost (CMV ÷ líquido)
e markup (P ÷ CMV) porque são os números que donos de restaurante conhecem.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

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


def avaliar_preco(preco: float, cmv: float, taxa: float = TAXA_PADRAO) -> dict:
    """Tudo que acontece com um preço ``P`` dado."""
    liquido = preco * (1.0 - taxa)
    lucro = liquido - cmv
    return {
        "preco": round(preco, 2),
        "taxa_plataforma": round(preco * taxa, 2),
        "recebe_liquido": round(liquido, 2),
        "cmv": round(cmv, 2),
        "lucro": round(lucro, 2),
        "margem_sobre_liquido": round(lucro / liquido, 4) if liquido else None,
        "margem_sobre_preco": round(lucro / preco, 4) if preco else None,
        "food_cost": round(cmv / liquido, 4) if liquido else None,
        "markup": round(preco / cmv, 2) if cmv else None,
        "viavel": lucro >= -1e-9,
        "explicacao": (
            f"Preço {brl(preco)} → taxa 10% = {brl(preco * taxa)} → Dona Maria recebe {brl(liquido)} "
            f"→ menos CMV {brl(cmv)} = lucro {brl(lucro)}"
            + (f" ({pct(lucro / liquido)} do que recebe)" if liquido else "")
        ),
    }


def cenarios_preco(
    cmv_porcao: float,
    taxa: float = TAXA_PADRAO,
    margens: Optional[Iterable[float]] = None,
    nomes: Optional[Iterable[str]] = None,
    arredondar_para: float = 0.50,
) -> dict:
    cmv = float(cmv_porcao)
    taxa = float(taxa)
    margens_lista = [float(m) for m in (margens or MARGENS_PADRAO)]
    nomes_lista = list(nomes or NOMES_PADRAO)
    minimo = preco_minimo(cmv, taxa)
    saida = {
        "cmv_por_porcao": round(cmv, 2),
        "taxa_plataforma": taxa,
        "preco_minimo": round(minimo, 2),
        "preco_minimo_explicacao": (
            f"Preço mínimo para não perder dinheiro: P = CMV ÷ (1 − {pct(taxa)}) = {brl(cmv)} ÷ {1 - taxa:.2f} = {brl(minimo)}. "
            f"Nesse preço o lucro é zero."
        ),
        "formula": "P = CMV ÷ (0,90 × (1 − margem)); recebe = 0,90 × P; lucro = recebe − CMV",
        "cenarios": [],
    }
    for i, margem in enumerate(margens_lista):
        if margem >= 1:
            continue
        preco_exato = cmv / ((1.0 - taxa) * (1.0 - margem))
        sugerido = round_up_to(preco_exato, arredondar_para)
        nome = nomes_lista[i] if i < len(nomes_lista) else f"Cenário {i + 1}"
        saida["cenarios"].append({
            "nome": nome,
            "margem_alvo": margem,
            "preco_exato": round(preco_exato, 2),
            "preco_sugerido": sugerido,
            "calculo": (
                f"P = {brl(cmv)} ÷ (0,90 × (1 − {pct(margem)})) = {brl(cmv)} ÷ {(1 - taxa) * (1 - margem):.3f} = {brl(preco_exato)} "
                f"→ arredondado para {brl(sugerido)}"
            ),
            "com_preco_sugerido": avaliar_preco(sugerido, cmv, taxa),
        })
    return saida


def simular_precos(cmv_porcao: float, precos: Iterable[float], taxa: float = TAXA_PADRAO) -> list[dict]:
    return [avaliar_preco(float(p), float(cmv_porcao), taxa) for p in precos]
