"""Garante que o reset não mexe na configuração e permite recuperar os dados."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reset_dados import resetar


class ResetDadosTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.projeto = Path(temp.name)
        self.home = self.projeto / "hermes-home"
        self.dados = {
            "memories/MEMORY.md": "conversa e estoque antigos",
            "sessions/sessao-1.jsonl": "mensagem antiga",
            ".hermes_history": "pergunta anterior",
            "sabor_da_maria/sabor_da_maria.db": "estoque anterior",
            "sabor_da_maria/sabor_da_maria.db-wal": "mudanças antes do checkpoint",
            "state.db": "chat anterior",
            "telemetry/shared_metrics/metrics.sqlite3": "métricas anteriores",
        }
        for nome, conteudo in {**self.dados, "config.yaml": "configuração"}.items():
            arquivo = self.home / nome
            arquivo.parent.mkdir(exist_ok=True, parents=True)
            arquivo.write_text(conteudo)

    def test_arquiva_dados_e_troca_geracao_das_conversas(self):
        internos = self.projeto / "database-internals"
        for nome in ("conversas.sqlite3", "memoria/chat.sqlite3", "kv/prefixo.bin", "contexto/referencia.md"):
            arquivo = internos / nome
            arquivo.parent.mkdir(exist_ok=True, parents=True)
            arquivo.write_text(nome)
        copia = resetar(self.projeto)
        for nome, conteudo in self.dados.items():
            self.assertEqual((copia / nome).read_text(), conteudo)
            if nome.startswith("memories/"):
                self.assertNotIn(conteudo, (self.home / nome).read_text())
            else:
                self.assertFalse((self.home / nome).exists())
        self.assertEqual((self.home / "config.yaml").read_text(), "configuração")
        for nome in ("conversas.sqlite3", "memoria/chat.sqlite3", "kv/prefixo.bin"):
            self.assertFalse((internos / nome).exists())
            self.assertEqual((copia / "database-internals" / nome).read_text(), nome)
        self.assertTrue((internos / "contexto/referencia.md").exists())
        geracao = self.home / "sabor_da_maria/.geracao-dados"
        antes = geracao.read_text()
        self.assertTrue(antes)
        resetar(self.projeto)
        self.assertNotEqual(geracao.read_text(), antes)

    def test_recompõe_dados_se_nao_puder_criar_nova_geracao(self):
        escrever = Path.write_text

        def falhar_geracao(arquivo, *args, **kwargs):
            if arquivo.name == ".geracao-dados":
                raise OSError("sem espaço")
            return escrever(arquivo, *args, **kwargs)

        with patch.object(Path, "write_text", falhar_geracao), self.assertRaisesRegex(OSError, "sem espaço"):
            resetar(self.projeto)
        for nome, conteudo in self.dados.items():
            self.assertEqual((self.home / nome).read_text(), conteudo)


if __name__ == "__main__":
    unittest.main()
