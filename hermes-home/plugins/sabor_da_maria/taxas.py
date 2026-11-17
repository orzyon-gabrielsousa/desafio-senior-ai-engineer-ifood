"""Entradas da simulação de preço por porção e referências públicas dos planos."""

from __future__ import annotations

import math
from typing import Any

PLANOS = {
    "basico": {"rotulo": "iFood Básico", "comissao": 0.12, "pagamento": 0.032},
    "entrega": {"rotulo": "iFood Entrega", "comissao": 0.23, "pagamento": 0.032},
    "personalizado": {"rotulo": "Meu contrato", "comissao": 0.12, "pagamento": 0.032},
    "desafio": {"rotulo": "Simulação original · 10%", "comissao": 0.10, "pagamento": 0.0},
}
REFERENCIAS = {
    "consultadas_em": "2026-11-16",
    "ifood": "https://blog-parceiros.ifood.com.br/planos-ifood/",
    "simples": "https://legis.senado.gov.br/norma/572878/publicacao/34621013",
}


def _percentual(valor: Any, rotulo: str) -> float:
    if type(valor) not in (float, int) or not math.isfinite(valor) or not 0 <= valor < 1:
        raise ValueError(f"informe {rotulo} como percentual de 0 a menos de 100%")
    arredondado = round(valor, 4)
    if abs(valor - arredondado) > 1e-10:
        raise ValueError(f"informe {rotulo} com até duas casas decimais em %")
    return arredondado


def normalizar(dados: dict[str, Any]) -> dict[str, Any]:
    plano = dados.get("plano", "basico")
    if not isinstance(plano, str) or plano not in PLANOS:
        raise ValueError("escolha um plano de taxas válido")
    referencia = PLANOS[plano]
    if plano == "personalizado":
        comissao = _percentual(dados.get("comissao", referencia["comissao"]), "a comissão")
        pagamento = _percentual(dados.get("pagamento", referencia["pagamento"]), "a taxa de pagamento")
    else:
        comissao, pagamento = referencia["comissao"], referencia["pagamento"]
    online = dados.get("pagamento_online", True)
    if not isinstance(online, bool):
        raise ValueError("informe se o pagamento é feito pelo iFood")
    imposto = dados.get("imposto")
    if imposto is not None:
        imposto = _percentual(imposto, "a alíquota efetiva de impostos")
    perfil = {"plano": plano, "comissao": comissao, "pagamento": pagamento, "pagamento_online": online, "imposto": imposto}
    if total(perfil) >= 1:
        raise ValueError("a soma dos descontos sobre a venda deve ser menor que 100%")
    return perfil


def componentes(perfil: dict[str, Any]) -> tuple[float, float, float]:
    return (perfil["comissao"], perfil["pagamento"] if perfil["pagamento_online"] else 0.0, perfil["imposto"] or 0.0)


def total(perfil: dict[str, Any]) -> float:
    return round(sum(componentes(perfil)), 12)
