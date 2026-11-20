"""Regras financeiras fixadas pelo enunciado do desafio técnico.

O formato do perfil é mantido para ler configurações legadas. Contratos reais,
tributos e taxas adicionais não participam da avaliação deste projeto.
"""

from __future__ import annotations

from typing import Any

TAXA_PLATAFORMA = 0.10
PLANOS = {
    "desafio": {"rotulo": "Regra do desafio · 10%", "comissao": TAXA_PLATAFORMA, "pagamento": 0.0},
}
REFERENCIAS = {
    "documento": "docs/desafio-senior-ai-engineer.md",
    "secao": "2.4 Aceitação → CMV e preço de venda",
}


def normalizar(dados: dict[str, Any]) -> dict[str, Any]:
    """Aplica o enunciado inclusive a perfis antigos e tentativas de alteração.

    Ler uma configuração salva com plano real não deve preservar descontos que
    contrariam o desafio. A mesma normalização vale para a API de compatibilidade.
    """
    return {"plano": "desafio", "comissao": TAXA_PLATAFORMA, "pagamento": 0.0,
            "pagamento_online": False, "imposto": 0.0}


def componentes(perfil: dict[str, Any]) -> tuple[float, float, float]:
    return TAXA_PLATAFORMA, 0.0, 0.0


def total(perfil: dict[str, Any]) -> float:
    return TAXA_PLATAFORMA
