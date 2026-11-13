import unittest

from sabor_da_maria.matching import best_matches, score
from sabor_da_maria.store import Store

from ._base import StoreTestCase

PANTRY = [
    "Arroz branco tipo 1", "Feijão carioquinha", "Feijão preto", "Peito de frango", "Carne moída (patinho)",
    "Carne de panela (acém)", "Miolo de alcatra", "Bacon", "Ovos", "Farinha de trigo", "Farinha de mandioca",
    "Macarrão espaguete", "Polenta (fubá)", "Batata", "Queijo mussarela", "Queijo parmesão ralado", "Tomate",
    "Cebola", "Alho", "Óleo de soja", "Manteiga", "Sal", "Açúcar", "Leite integral", "Couve",
    "Salsinha (cheiro-verde)", "Caldo de carne (tempero)", "Açafrão em pó (cúrcuma)", "Alcaparras",
    "Amêndoa fatiada", "Chantilly", "Leite ninho em pó", "Cobertura de chocolate", "Azeite de oliva extra virgem",
    "Aceto balsâmico", "Canela em pó", "Adoçante líquido",
]


class MatchingTests(unittest.TestCase):
    def _top(self, query):
        matches = best_matches(query, PANTRY)
        return matches[0]["nome"] if matches else None

    def test_synonyms_and_fuzzy(self):
        self.assertEqual(self._top("cheiro-verde"), "Salsinha (cheiro-verde)")
        self.assertEqual(self._top("queijo ralado"), "Queijo parmesão ralado")
        self.assertEqual(self._top("fubá"), "Polenta (fubá)")
        self.assertEqual(self._top("carne moída"), "Carne moída (patinho)")
        self.assertEqual(self._top("1 cebola média picada"), "Cebola")
        self.assertEqual(self._top("dentes de alho"), "Alho")
        self.assertEqual(self._top("ovo"), "Ovos")
        self.assertEqual(self._top("azeite"), "Azeite de oliva extra virgem")
        self.assertEqual(self._top("leite em pó"), "Leite ninho em pó")
        self.assertEqual(self._top("filé de frango"), "Peito de frango")

    def test_ambiguous_or_absent(self):
        feijao = best_matches("feijão", PANTRY)
        self.assertEqual({m["nome"] for m in feijao[:2]}, {"Feijão carioquinha", "Feijão preto"})
        condensado = best_matches("leite condensado", PANTRY)
        self.assertFalse(condensado and condensado[0]["confiavel"] and condensado[0]["nome"] == "Leite integral" and condensado[0]["score"] > 0.9)
        self.assertEqual(best_matches("abacaxi", PANTRY), [])

    def test_score_bounds(self):
        self.assertEqual(score("Cebola", "Cebola"), 1.0)
        self.assertEqual(score("", "Cebola"), 0.0)


class StoreTests(StoreTestCase):
    def test_load_and_unit_costs(self):
        store = Store()
        info = store.ensure_loaded()
        self.assertTrue(info["carregado"])
        self.assertEqual(info["itens"], 37)
        itens = {i["nome"]: i for i in store.ingredientes()}
        self.assertEqual(len(itens), 37)
        self.assertAlmostEqual(itens["Alcaparras"]["custo_unitario_base"], 41.0)
        self.assertEqual(itens["Alcaparras"]["estoque_base"], 2.0)
        self.assertAlmostEqual(itens["Chantilly"]["custo_unitario_base"], 47.34)
        self.assertAlmostEqual(itens["Azeite de oliva extra virgem"]["custo_unitario_base"], 61.98)
        self.assertAlmostEqual(itens["Adoçante líquido"]["custo_unitario_base"], 19.0)
        self.assertAlmostEqual(itens["Ovos"]["custo_unitario_base"], 0.8)
        self.assertAlmostEqual(itens["Arroz branco tipo 1"]["custo_unitario_base"], 4.98)
        self.assertIsNone(itens["Cobertura de chocolate"]["conteudo_un_base"])
        self.assertIn("pergunte", itens["Cobertura de chocolate"]["observacao"])
        # segunda chamada não recarrega
        self.assertFalse(store.ensure_loaded()["carregado"])

    def test_search_update_content_and_purchase(self):
        store = Store()
        cand = store.buscar("queijo ralado")
        self.assertEqual(cand[0]["nome"], "Queijo parmesão ralado")
        store.atualizar_conteudo("Cobertura de chocolate", 1.01, "kg")
        self.assertAlmostEqual(store.obter("Cobertura de chocolate")["conteudo_un_base"], 1.01)
        cebola = store.registrar_compra("Cebola", 1, "kg", 6.0)
        self.assertAlmostEqual(cebola["estoque_base"], 2.0)
        self.assertAlmostEqual(cebola["custo_unitario_base"], 5.0)  # (4 + 6) / 2 kg
        novo = store.registrar_compra("Creme de leite", 2, "un 200g", 7.0, novo=True)
        self.assertAlmostEqual(novo["estoque_base"], 0.4)
        self.assertAlmostEqual(novo["custo_unitario_base"], 17.5)

    def test_profile_merge_and_budget(self):
        store = Store()
        store.perfil_salvar({"equipamentos": {"forno": True}, "restricoes": {"gas": "botijão ok"}})
        perfil = store.perfil_salvar({"equipamentos": {"panela_pressao": False}, "observacoes": "cozinha pequena"})
        self.assertEqual(perfil["equipamentos"], {"forno": True, "panela_pressao": False})
        self.assertEqual(perfil["restricoes"], {"gas": "botijão ok"})
        self.assertEqual(perfil["observacoes"], ["cozinha pequena"])
        orc = store.orcamento()
        self.assertEqual((orc["inicial"], orc["comprometido"], orc["restante"]), (80.0, 0.0, 80.0))


if __name__ == "__main__":
    unittest.main()
