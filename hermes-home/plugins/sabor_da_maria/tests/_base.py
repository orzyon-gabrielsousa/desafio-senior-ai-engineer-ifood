"""Infra comum dos testes: planilha real do desafio + banco SQLite temporário."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
XLSX = REPO / "data" / "despensa_dona_maria.xlsx"


class StoreTestCase(unittest.TestCase):
    """Cada teste ganha um HERMES_HOME temporário apontando para a planilha real."""

    def setUp(self) -> None:
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
