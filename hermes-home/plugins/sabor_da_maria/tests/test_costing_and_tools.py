import json
import unittest

from sabor_da_maria import costing, tools

from ._base import StoreTestCase


class CostingTests(unittest.TestCase):
    def test_minimum_price_and_evaluation(self):
        self.assertAlmostEqual(costing.preco_minimo(9.0), 10.0)
        aval = costing.avaliar_preco(20.0, 9.0)
        self.assertEqual((aval["recebe_liquido"], aval["lucro"]), (18.0, 9.0))
        self.assertAlmostEqual(aval["margem_sobre_liquido"], 0.5)
        self.assertAlmostEqual(aval["food_cost"], 0.5)
        self.assertFalse(costing.avaliar_preco(9.0, 9.0)["viavel"])

    def test_scenarios(self):
        out = costing.cenarios_preco(9.0)
        self.assertEqual(len(out["cenarios"]), 3)
        self.assertAlmostEqual(out["preco_minimo"], 10.0)
        equilibrado = out["cenarios"][1]
        self.assertAlmostEqual(equilibrado["preco_exato"], 20.0)
        self.assertEqual(equilibrado["preco_sugerido"], 20.0)
        self.assertEqual(out["cenarios"][0]["preco_sugerido"], 14.5)  # 9 / (0.9*0.7) = 14.29 -> 14.50
        self.assertIn("R$ 9,00", out["preco_minimo_explicacao"])

    def test_brl_and_rounding(self):
        self.assertEqual(costing.brl(1234.5), "R$ 1.234,50")
        self.assertEqual(costing.brl(-3.1), "-R$ 3,10")
        self.assertEqual(costing.round_up_to(23.1, 0.5), 23.5)
        self.assertEqual(costing.round_up_to(23.5, 0.5), 23.5)


def call(name, **args):
    return json.loads(tools.HANDLERS[name](args))


class ToolsFlowTests(StoreTestCase):
    def test_full_flow(self):
        listagem = call("despensa_listar")
        self.assertTrue(listagem["success"])
        self.assertEqual(listagem["total_itens"], 37)
        self.assertEqual(listagem["orcamento"]["restante"], 80.0)

        busca = call("despensa_buscar", ingredientes=["cheiro verde", "abacaxi"])
        self.assertTrue(busca["resultados"][0]["encontrado"])
        self.assertFalse(busca["resultados"][1]["encontrado"])

        perfil = call("perfil_cozinha_consultar")
        self.assertEqual(len(perfil["pendencias"]), 10)
        salvo = call("perfil_cozinha_salvar", equipamentos={"fogão": 4, "forno": "sim", "panela de pressão": True, "air fryer": "não", "liquidificador": True},
                     restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão ok", "energia": "estável"},
                     tecnicas={"refogado": "sim", "massa fresca": "nunca fiz", "molho branco": "aprendendo"})
        self.assertTrue(salvo["completo"], salvo["pendencias"])
        self.assertEqual(salvo["perfil"]["equipamentos"]["fogao"], 4)

        receita = call(
            "receita_conferir_ingredientes",
            nome="Frango com batatas ao forno", porcoes=4, fonte_url="https://exemplo.com/frango",
            ingredientes=[
                {"nome": "peito de frango", "quantidade": 1, "unidade": "kg"},
                {"nome": "batata", "quantidade": 1, "unidade": "kg"},
                {"nome": "cebola média", "quantidade": 1, "unidade": "un"},
                {"nome": "dentes de alho", "quantidade": 3, "unidade": "dente"},
                {"nome": "azeite", "quantidade": 2, "unidade": "colher de sopa"},
                {"nome": "creme de leite", "quantidade": 200, "unidade": "g", "item_despensa": "", "preco_compra_estimado": 3.5, "quantidade_compra": 200, "unidade_compra": "g"},
                {"nome": "sal", "quantidade": 5, "unidade": "g"},
            ],
            equipamentos_necessarios=["forno", "liquidificador", "batedeira"],
            tecnicas_necessarias=["refogado", "massa fresca", "ponto de carne"],
            tempo_preparo_min=60,
        )
        self.assertTrue(receita["success"], receita)
        self.assertEqual(receita["veredito"], "inviavel_por_enquanto")  # massa fresca = nunca fiz
        self.assertEqual(receita["equipamentos"]["ok"], ["forno", "liquidificador"])
        self.assertEqual(receita["equipamentos"]["nao_informado"], ["batedeira"])
        self.assertEqual(receita["tecnicas"]["faltando"], ["massa fresca"])
        self.assertEqual(receita["tecnicas"]["nao_informado"], ["ponto de carne"])
        resumo = receita["resumo"]
        self.assertIn("peito de frango", resumo["ja_tem_tudo_que_precisa"])
        self.assertEqual(resumo["nao_tem"], ["creme de leite"])
        self.assertEqual(resumo["compras_total_estimado"], 3.5)
        self.assertTrue(resumo["orcamento"]["cabe_no_orcamento"])
        frango = next(i for i in receita["ingredientes"] if i["ingrediente"] == "peito de frango")
        self.assertEqual(frango["custo_uso_total"], 14.0)  # 1 kg × R$ 14,00/kg
        azeite = next(i for i in receita["ingredientes"] if i["ingrediente"] == "azeite")
        self.assertTrue(azeite["estimativa"])
        self.assertAlmostEqual(azeite["custo_uso_total"], round(0.03 * 61.98, 2))

        cmv = call("prato_calcular_cmv", nome="Frango com batatas", porcoes=4, receita_id=receita["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        esperado = 14.0 + 6.0 + 0.6 + 0.3 + round(0.03 * 61.98, 4) + 3.5 + round(0.005 * 1.85, 4)
        self.assertAlmostEqual(cmv["cmv_total"], round(esperado, 2), places=1)
        self.assertAlmostEqual(cmv["cmv_por_porcao"], round(cmv["cmv_total"] / 4, 2))
        self.assertTrue(any("CMV por porção" in linha for linha in cmv["memoria_de_calculo"]))

        preco = call("preco_sugerir", cmv_por_porcao=cmv["cmv_por_porcao"], prato_id=cmv["prato_id"], precos_para_simular=[15, 25])
        self.assertEqual(len(preco["cenarios"]), 3)
        self.assertEqual(len(preco["simulacoes"]), 2)
        self.assertAlmostEqual(preco["preco_minimo"], round(cmv["cmv_por_porcao"] / 0.9, 2))

        registro = call("cardapio_registrar", prato_id=cmv["prato_id"], preco_escolhido=preco["cenarios"][1]["preco_sugerido"])
        self.assertTrue(registro["success"], registro)
        self.assertEqual(registro["orcamento"]["comprometido"], 3.5)
        self.assertEqual(registro["orcamento"]["restante"], 76.5)

        cardapio = call("cardapio_consultar")
        self.assertEqual(len(cardapio["cardapio"]), 1)
        self.assertEqual(cardapio["cardapio"][0]["nome"], "Frango com batatas")
        self.assertGreater(cardapio["cardapio"][0]["lucro_por_porcao"], 0)

    def test_missing_quantities_and_unknown_content_become_pendencias(self):
        out = call("receita_conferir_ingredientes", nome="Bolo", porcoes=8, ingredientes=[
            {"nome": "cobertura de chocolate", "quantidade": 200, "unidade": "g"},
            {"nome": "farinha de trigo", "quantidade": 0, "unidade": "g"},
            {"nome": "leite condensado", "quantidade": 1, "unidade": "lata"},
        ])
        self.assertEqual(out["veredito"], "precisa_confirmar")
        self.assertEqual(len(out["pendencias"]), 3)
        self.assertTrue(any("despensa_atualizar" in p for p in out["pendencias"]))
        call("despensa_atualizar", ingrediente="Cobertura de chocolate", conteudo_por_unidade={"quantidade": 1, "unidade": "kg"})
        out2 = call("receita_conferir_ingredientes", nome="Bolo", porcoes=8, ingredientes=[
            {"nome": "cobertura de chocolate", "quantidade": 200, "unidade": "g"},
        ])
        cob = out2["ingredientes"][0]
        self.assertEqual(cob["custo_uso_total"], 15.98)  # 0,2 un × R$ 79,90

    def test_insufficient_stock_is_flagged_and_priced(self):
        out = call("receita_conferir_ingredientes", nome="Strogonoff", porcoes=6, ingredientes=[
            {"nome": "alcatra", "quantidade": 1.2, "unidade": "kg"},
        ])
        item = out["ingredientes"][0]
        self.assertEqual(item["item_despensa"], "Miolo de alcatra")
        self.assertEqual(item["falta"], "400 g")
        self.assertEqual(item["compra"]["preco_estimado"], 17.0)  # 0,4 kg × R$ 42,50/kg
        self.assertEqual(out["resumo"]["tem_mas_nao_o_suficiente"], ["alcatra"])

    def test_errors_are_returned_not_raised(self):
        out = call("prato_calcular_cmv", nome="x", porcoes=0, ingredientes=[{"nome": "sal", "quantidade": 1, "unidade": "g"}])
        self.assertFalse(out["success"])
        self.assertIn("porcoes", out["error"])


if __name__ == "__main__":
    unittest.main()
