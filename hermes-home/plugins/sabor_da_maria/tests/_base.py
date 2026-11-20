"""Infra comum dos testes: planilha real do desafio + banco SQLite temporário."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sabor_da_maria import falas, pesquisa

REPO = Path(__file__).resolve().parents[4]
XLSX = REPO / "data" / "despensa_dona_maria.xlsx"

SESSAO = "sessao-de-teste"

# Tudo o que o roteiro da demo faz a Dona Maria contar sobre a cozinha, numa fala só.
FALA_COZINHA = (
    "Tenho fogão de 4 bocas, forno, panela de pressão e liquidificador. Não tenho air fryer. "
    "A geladeira é média, tenho até 2 horas por cozinhada, o gás é de botijão e a energia é estável. "
    "Sei refogar, nunca fiz massa fresca e molho branco estou aprendendo."
)


def diz(*textos: str, sessao: str = SESSAO) -> None:
    """A Dona Maria fala: o plugin passa a ter essas falas, como depois do pre_llm_call do Hermes."""
    falas.registrar(sessao, [{"role": "user", "content": t} for t in textos])


class StoreTestCase(unittest.TestCase):
    """Cada teste ganha um HERMES_HOME temporário apontando para a planilha real."""

    def setUp(self) -> None:
        falas.esquecer_tudo()
        pesquisa.esquecer_tudo()
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k) for k in ("SABOR_DA_MARIA_DB", "SABOR_DA_MARIA_XLSX", "SABOR_DA_MARIA_ORCAMENTO", "HERMES_HOME")}
        os.environ["HERMES_HOME"] = self._tmp.name
        os.environ["SABOR_DA_MARIA_DB"] = str(Path(self._tmp.name) / "sabor.db")
        os.environ["SABOR_DA_MARIA_XLSX"] = str(XLSX)
        os.environ["SABOR_DA_MARIA_ORCAMENTO"] = "80"

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()
        falas.esquecer_tudo()
        pesquisa.esquecer_tudo()
