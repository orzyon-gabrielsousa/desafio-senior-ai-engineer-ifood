"""Verifica se a medição conta chamadas e escolhe a versão realmente apresentada."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from avaliar_fluxo import source_snapshots, tool_metrics, visible_recipe
from fluxo_assertions import assert_contract


class MetricasDoFluxoTests(unittest.TestCase):
    def test_tempo_proprio_da_adaptacao_nao_vira_duracao_publicada(self):
        url = "https://receitas.exemplo/salada"
        page = {"url": url, "content": "# Salada\n## Ingredientes\n- 100 g de tomate\n## Modo de preparo\nCorte o tomate."}
        recipe = {"fonte_url": url, "porcoes": 1, "dados": {
            "tempo_preparo_min": 10, "ingredientes": [{"nome": "tomate", "quantidade": 100, "unidade": "g"}],
            "fonte_verificada": {"url": url, "nome": "Salada", "ingredientes": ["100 g de tomate"],
                                   "porcoes": 1, "modo_preparo": "Corte o tomate.", "tempo_preparo_min": None}}}
        with self.assertRaisesRegex(AssertionError, "duração publicada"):
            assert_contract(recipe, [page], "Salada pronta em 10 minutos.")
        recipe["dados"]["adaptacao"] = "Versão da casa com tempo próprio estimado."
        with self.assertRaisesRegex(AssertionError, "estimativa"):
            assert_contract(recipe, [page], "Salada pronta em 10 minutos.")
        result = assert_contract(recipe, [page], "Adaptação da casa. Preparo em cerca de 10 minutos.")
        self.assertTrue(result["versao_adaptada"])
        recipe["dados"]["fonte_verificada"]["porcoes"] = None
        with self.assertRaisesRegex(AssertionError, "rendimento próprio"):
            assert_contract(recipe, [page], "Adaptação da casa. Preparo em cerca de 10 minutos.")
        result = assert_contract(recipe, [page], "Adaptação da casa, rendimento inicial de 1 porção. Preparo em cerca de 10 minutos.")
        self.assertTrue(result["versao_adaptada"])

    def test_eventos_duplicados_e_ferramentas_sobrepostas_nao_inflam_medicao(self):
        events = [
            {"toolCallId": "a", "tool": "busca", "status": "running", "segundos": 1},
            {"toolCallId": "b", "tool": "leitura", "status": "running", "segundos": 2},
            {"toolCallId": "b", "tool": "leitura", "status": "completed", "segundos": 3},
            {"toolCallId": "a", "tool": "busca", "status": "completed", "segundos": 4},
        ]
        metrics = tool_metrics(events, 5)
        self.assertEqual(metrics["chamadas"], 2)
        self.assertEqual(metrics["tempo_em_ferramentas_s"], 3)
        self.assertEqual(metrics["tempo_fora_ferramentas_s"], 2)

    def test_proposta_apresentada_tem_precedencia_sobre_ficha_bloqueada_mais_recente(self):
        state = {"pratos": [{"id": 2, "receita_id": 20}, {"id": 3, "receita_id": 30}],
                 "receitas": [{"id": 20, "nome": "Versão da casa", "dados": {"veredito": "viavel"}},
                              {"id": 30, "nome": "Outra receita", "dados": {"veredito": "precisa_confirmar"}}]}
        self.assertEqual(visible_recipe(state, "Proponho o prato #2, Versão da casa.")["id"], 20)
        self.assertIsNone(visible_recipe(state, "O prato #3 tem uma pendência."))

    def test_snapshot_usa_resultado_de_ferramenta_e_ignora_fonte_inventada_na_resposta(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            page = {"url": "https://fonte.exemplo/receita", "content": "## Ingredientes\n- arroz\n## Modo de preparo\nCozinhe."}
            fake = {**page, "url": "https://inventada.exemplo/receita"}
            with sqlite3.connect(home / "state.db") as conn:
                conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT, api_content TEXT)")
                conn.execute("INSERT INTO messages(role,content) VALUES(?,?)", ("assistant", json.dumps(fake)))
                conn.execute("INSERT INTO messages(role,content) VALUES(?,?)",
                             ("tool", "<tool_result>\n" + json.dumps({"results": [page]}) + "\n</tool_result>"))
            self.assertEqual(source_snapshots(home), [page])


if __name__ == "__main__":
    unittest.main()
