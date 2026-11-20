"""A adaptação usa as faltas conferidas e só retira ingredientes sustentados pelo preparo."""

import unittest

from sabor_da_maria import adaptacao_estoque


class AdaptacaoEstoqueTests(unittest.TestCase):
    def pizza_com_cobertura(self):
        nome = "Pizza para delivery"
        ingredientes = [
            {"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"},
            {"nome": "Água morna", "quantidade": 300, "unidade": "ml"},
            {"nome": "Molho de tomate", "quantidade": 300, "unidade": "g", "item_despensa": "Tomate"},
            {"nome": "Queijo mussarela", "quantidade": 500, "unidade": "g"},
            {"nome": "Cebola", "quantidade": 1, "unidade": "kg"},
            {"nome": "Tomate", "quantidade": 2, "unidade": "kg"},
        ]
        receita = {"nome": nome, "porcoes": 6, "dados": {
            "ingredientes": ingredientes,
            "modo_preparo": "Misture a farinha e o fermento biológico seco. Cubra com presunto e tomate.",
            "fonte_verificada": {"nome": nome, "porcoes": 6, "ingredientes": ["1 cebola fatiada", "1 tomate em rodelas"]},
        }}
        estoque = {"faltantes": [], "itens": [
            {"ingrediente": "Farinha de trigo", "necessario_base": 0.5, "unidade_base": "kg", "falta": "nada"},
            {"ingrediente": "Água morna", "necessario_base": 0.3, "unidade_base": "L", "falta": "nada"},
        ]}
        return receita, estoque

    def test_primeira_adaptacao_de_pizza_generica_corrige_preparo_e_medidas(self):
        receita, estoque = self.pizza_com_cobertura()
        extras = ["Fermento biológico seco", "Presunto", "Calabresa", "Orégano"]
        receita["dados"]["ingredientes"].extend({"nome": n, "quantidade": 7, "unidade": "g"} for n in extras)
        estoque["faltantes"] = [{"ingrediente": n} for n in extras]
        args, omitidos = adaptacao_estoque.propor(receita, estoque, "Fermento biológico seco")
        self.assertEqual(set(omitidos), set(extras))
        self.assertEqual(args["porcoes"], 6)
        linhas = {i["nome"]: (i["quantidade"], i["unidade"]) for i in args["ingredientes"]}
        self.assertEqual(linhas["Tomate"], (1, "un"))
        self.assertEqual(linhas["Cebola"], (1, "un"))
        self.assertEqual(linhas["Molho de tomate"], (300, "g"))
        self.assertIn("tomate fatiado", args["modo_preparo"])
        self.assertNotIn("fermento", args["modo_preparo"].lower())
        self.assertNotIn("presunto", args["modo_preparo"].lower())

    def test_corrige_preparo_incompativel_com_omissao_respeitando_medida_confirmada(self):
        receita, estoque = self.pizza_com_cobertura()
        receita["dados"].update(ingredientes_omitidos=["Fermento biológico seco", "Presunto"],
                                medidas_de_uso_confirmadas={"Cebola": {"quantidade": 1, "unidade": "kg"}})
        args, _ = adaptacao_estoque.propor(receita, estoque)
        linhas = {i["nome"]: (i["quantidade"], i["unidade"]) for i in args["ingredientes"]}
        self.assertEqual(linhas["Cebola"], (1, "kg"))
        self.assertEqual(linhas["Tomate"], (1, "un"))
        self.assertNotIn("fermento", args["modo_preparo"].lower())
        receita["dados"]["modo_preparo"] = "Misture os ingredientes sem adicionar fermento biológico. Não adicione fermento."
        self.assertIsNone(adaptacao_estoque.propor(receita, estoque))

    def test_retirar_tempero_mantem_ingrediente_principal_pendente(self):
        abobora = {"nome": "Abóbora", "quantidade": 300, "unidade": "g"}
        oregano = {"nome": "Orégano a gosto"}
        receita = {"nome": "Sopa de abóbora", "porcoes": 4, "dados": {
            "ingredientes": [abobora, oregano], "modo_preparo": "Cozinhe a abóbora. Finalize com orégano e sirva.",
            "tempo_preparo_min": 45,
        }}
        estoque = {"faltantes": [{"ingrediente": "Abóbora"}, {"ingrediente": "Orégano a gosto"}]}
        self.assertIsNone(adaptacao_estoque.propor(receita, estoque, "Abóbora"))
        args, omitidos = adaptacao_estoque.propor(receita, estoque, "Orégano")

        self.assertEqual(omitidos, ["Orégano"])
        self.assertEqual(args["ingredientes"], [abobora])
        self.assertEqual(args["tempo_preparo_min"], 45)
        self.assertIn("Cozinhe a abóbora", args["modo_preparo"])
        self.assertNotIn("orégano", args["modo_preparo"])
        self.assertEqual(receita["dados"]["ingredientes"], [abobora, oregano])

    def test_tempero_que_define_o_prato_permanece(self):
        receita = {"nome": "Molho de salsinha", "porcoes": 4, "dados": {
            "ingredientes": [{"nome": "Azeite", "quantidade": 20, "unidade": "ml"}, {"nome": "Salsinha"}],
            "modo_preparo": "Misture o azeite e a salsinha.",
        }}
        estoque = {"faltantes": [{"ingrediente": "Salsinha"}]}
        self.assertIsNone(adaptacao_estoque.propor(receita, estoque, "Salsinha"))

    def test_adaptacao_nao_muda_medida_ja_confirmada(self):
        agua = {"nome": "Água", "quantidade": 400, "unidade": "ml"}
        receita = {"nome": "Pizza de queijo", "porcoes": 4, "dados": {
            "ingredientes": [{"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"}, agua,
                             {"nome": "Fermento biológico", "quantidade": 5, "unidade": "g"},
                             {"nome": "Queijo mussarela", "quantidade": 100, "unidade": "g"}],
            "modo_preparo": "Prepare a massa fermentada e asse a pizza.",
            "medidas_de_uso_confirmadas": {"Água": {"quantidade": 400, "unidade": "ml"}},
        }}
        estoque = {"faltantes": [{"ingrediente": "Fermento biológico"}], "itens": [
            {"ingrediente": "Farinha de trigo", "necessario_base": 0.5, "unidade_base": "kg", "falta": "nada"},
            {"ingrediente": "Água", "necessario_base": 0.4, "unidade_base": "L", "falta": "nada"},
        ]}
        self.assertIsNone(adaptacao_estoque.propor(receita, estoque, "Fermento biológico"))
        self.assertEqual(agua["quantidade"], 400)
