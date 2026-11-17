import json
import os
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from sabor_da_maria import memoria


class MemoriaTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.raiz = Path(temp.name)
        ambiente = patch.dict(os.environ, {"SABOR_DA_MARIA_INTERNALS": temp.name, "SABOR_DA_MARIA_EMBEDDINGS_URL": ""})
        ambiente.start()
        self.addCleanup(ambiente.stop)

    def _ler(self, pergunta, sessao="outro"):
        contexto = memoria.ao_iniciar_turno(session_id=sessao, user_message=pergunta, platform="api_server")
        return json.loads(contexto["context"].splitlines()[-1]) if contexto else []

    def test_turno_persiste_e_e_tratado_como_citacao_de_outro_chat(self):
        turno = {"session_id": "original", "turn_id": "turno-1", "platform": "api_server",
                 "user_message": "O nome do meu cardápio é Sabor Paulista.\n\n[painel da tela: cadastro guiado no chat]",
                 "assistant_response": "Vamos lembrar de Sabor Paulista. [[cadastro:cozinha]]"}
        memoria.apos_turno(**turno)
        memoria.apos_turno(**turno)
        achados = self._ler("Você se lembra de Sabor Paulista?")
        self.assertEqual({item["papel"] for item in achados}, {"usuário", "consultora"})
        self.assertEqual(len(achados), 2)
        self.assertTrue(all(item["origem"] == "conversa anterior" and "cadastro" not in item["trecho"] for item in achados))
        self.assertCountEqual(self._ler("O que dissemos na última conversa?"), achados)
        self.assertEqual(self._ler("Sabor Paulista", "original"), [])

    def test_tombstone_oculta_e_excluir_remove_trechos_sem_resgatar_turnos_atrasados(self):
        turno = {"session_id": "apagar", "turn_id": "t1", "user_message": "Acerola é nosso suco favorito."}
        memoria.apos_turno(**turno)
        self.assertTrue(self._ler("acerola"))
        with closing(sqlite3.connect(self.raiz / "conversas.sqlite3")) as db, db:
            db.execute("CREATE TABLE exclusoes (session_id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO exclusoes VALUES ('apagar')")
        self.assertEqual(self._ler("acerola"), [])
        self.assertEqual(memoria.estatisticas()["sessoes_indexadas"], 0)
        memoria.excluir_sessao("apagar")
        memoria.apos_turno(**{**turno, "turn_id": "t2"})
        with memoria._conectar(self.raiz, leitura=True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trechos").fetchone()[0], 0)

    def test_importa_historico_anterior_de_hermes_sem_duplicar_e_sem_ferramentas(self):
        nativo = self.raiz / "hermes"
        nativo.mkdir()
        with closing(sqlite3.connect(nativo / "state.db")) as db, db:
            db.executescript("""
                CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, hidden INTEGER);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, active INTEGER);
                INSERT INTO sessions VALUES ('nativo', 'api_server', 0), ('outro', 'telegram', 0), ('oculto', 'api_server', 1);
                INSERT INTO messages (session_id, role, content, tool_calls, active) VALUES
                    ('nativo', 'user', 'Gosto de picadinho.', NULL, 1),
                    ('nativo', 'assistant', 'Vou preparar picadinho.', '[]', 1),
                    ('nativo', 'assistant', 'Interno picadinho.', '[{}]', 1),
                    ('nativo', 'tool', 'Ferramenta picadinho.', NULL, 1),
                    ('nativo', 'user', 'Editado picadinho.', NULL, 0),
                    ('outro', 'user', 'Privado picadinho.', NULL, 1),
                    ('oculto', 'user', 'Oculto picadinho.', NULL, 1);
            """)
        memoria.apos_turno(session_id="nativo", turn_id="t1", user_message="Gosto de picadinho.")
        with patch.dict(os.environ, {"HERMES_HOME": str(nativo)}):
            achados = self._ler("picadinho")
            self.assertEqual({i["trecho"] for i in achados}, {"Gosto de picadinho.", "Vou preparar picadinho."})
            self.assertEqual(self._ler("picadinho"), achados)
        with memoria._conectar(self.raiz, leitura=True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trechos").fetchone()[0], 2)

    def test_importa_apenas_textos_escolhidos_e_remove_versoes_antigas(self):
        pasta = self.raiz / "contexto"
        pasta.mkdir()
        original = pasta / "escolhido.md"
        original.write_text("## A sobremesa da Maria\nPreferência por brigadeiro.", encoding="utf-8")
        segredo = self.raiz / "segredo.txt"
        segredo.write_text("Salsa secreta", encoding="utf-8")
        (pasta / "link.md").symlink_to(segredo)
        (pasta / "executavel.py").write_text("Salsa secreta")
        (pasta / "grande.md").write_bytes(b"x" * (memoria.MAX_BYTES_ARQUIVO + 1))
        self.assertEqual(self._ler("brigadeiro")[0]["origem"], "escolhido.md")
        self.assertEqual(self._ler("Salsa"), [])
        original.write_text("Prefere goiabada", encoding="utf-8")
        self.assertEqual(self._ler("brigadeiro"), [])
        self.assertIn("goiabada", self._ler("goiabada")[0]["trecho"])
        original.unlink()
        self.assertEqual(self._ler("goiabada"), [])

    def test_indice_usa_tensor_float32_e_recupera_sem_palavra_compartilhada(self):
        memoria.apos_turno(session_id="origem", turn_id="t1", user_message="Quero vender feijoada.")
        zero = (0.0,) * (memoria.DIMENSOES - 1)
        vetor = (1.0, *zero)
        config = ("http://embeddings:8080/v1/embeddings", "embedding-test")
        with patch.object(memoria, "_embeddings", return_value=[vetor]), patch.object(memoria, "_config_embeddings", return_value=config):
            memoria._TRABALHO.acquire()
            memoria._preencher_vetores(self.raiz, config)
            with patch.object(memoria, "_agendar_vetores"):
                self.assertIn("feijoada", self._ler("prato de sábado")[0]["trecho"])
        self.assertEqual(memoria.estatisticas()["vetores_indexados"], 1)
        with memoria._conectar(self.raiz, leitura=True) as db:
            bruto, modelo = db.execute("SELECT vetor, modelo FROM trechos").fetchone()
        self.assertEqual(modelo, config[1])
        self.assertEqual(struct.unpack(f"<{memoria.DIMENSOES}f", bruto), vetor)

    def test_canais_e_enderecos_externos_ficam_fora_da_memoria(self):
        memoria.apos_turno(session_id="privado", turn_id="t1", user_message="Conversa de outro serviço.", platform="telegram")
        self.assertEqual(self._ler("privado"), [])
        self.assertEqual(memoria.estatisticas()["sessoes_indexadas"], 0)
        with patch.dict(os.environ, {"SABOR_DA_MARIA_EMBEDDINGS_URL": "https://exemplo.org/v1/embeddings"}):
            self.assertIsNone(memoria._config_embeddings())
