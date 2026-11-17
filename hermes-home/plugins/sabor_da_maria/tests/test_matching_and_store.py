import unittest

from sabor_da_maria.matching import best_matches, score
from sabor_da_maria.store import OrcamentoEstourado, Store

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
        self.assertFalse(any(m["confiavel"] for m in best_matches("12 coxinhas da asa de frango", PANTRY)))
        self.assertEqual(best_matches("coxinhas da asa de frango", ["Peito de frango", "Coxinha da asa de frango"])[0]["nome"],
                         "Coxinha da asa de frango")

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
        self.assertEqual(store.orcamento()["saldo"], 67.0)

    def test_compra_dos_complementos_debita_o_saldo_e_nao_duplica_no_retry(self):
        store = Store()
        compras = [
            {"nome": "Mix de pimentas", "quantidade": 50, "unidade": "g", "preco_total": 0.50},
            {"nome": "Tempero para aves", "quantidade": 0.05, "unidade": "kg", "preco_total": 0.75},
        ]
        frango_antes = store.obter("Peito de frango")
        lote = store.registrar_compras(compras, chave="sessão/turno")
        self.assertEqual(lote["total"], 1.25)
        self.assertFalse(lote["ja_registrado"])
        self.assertEqual(lote["orcamento"]["saldo"], 78.75)
        self.assertEqual([i["estoque_base"] for i in lote["ingredientes"]], [0.05, 0.05])
        self.assertEqual([i["custo_unitario_base"] for i in lote["ingredientes"]], [10.0, 15.0])
        self.assertEqual(store.obter("Peito de frango"), frango_antes)

        outro = Store()
        retry = outro.registrar_compras(list(reversed(compras)), chave="sessão/turno")
        self.assertTrue(retry["ja_registrado"])
        self.assertEqual(outro.orcamento()["saldo"], 78.75)
        self.assertEqual(len(outro.orcamento()["compras_confirmadas"]), 2)
        self.assertEqual(outro.obter("Mix de pimentas")["estoque_base"], 0.05)
        with self.assertRaisesRegex(ValueError, "essa confirmação já registrou"):
            outro.registrar_compras([compras[0]], chave="sessão/turno")

    def test_lote_invalido_reverte_estoque_carrinho_e_orcamento(self):
        store = Store()
        carrinho = store.carrinho_adicionar("Mix de pimentas", 0.50, 50, "g")
        compras = [
            {"nome": "Mix de pimentas", "quantidade": 50, "unidade": "g", "preco_total": 0.50,
             "carrinho_id": carrinho["id"]},
            {"nome": "Cebola", "quantidade": 1, "unidade": "L", "preco_total": 1},
        ]
        with self.assertRaisesRegex(ValueError, "é controlado em kg"):
            store.registrar_compras(compras)
        self.assertIsNone(store.obter("Mix de pimentas"))
        self.assertEqual(store.carrinho_itens(), [carrinho])
        self.assertEqual(store.orcamento()["compras_confirmadas"], [])
        self.assertEqual(store.orcamento()["saldo"], 79.50)
        with self.assertRaisesRegex(ValueError, "quantidade de"):
            store.registrar_compras([{"nome": "Cebola", "unidade": "kg", "preco_total": 1}])
        with self.assertRaises(OrcamentoEstourado):
            store.registrar_compras([{**compras[0], "preco_total": 90}])
        self.assertEqual(store.carrinho_itens(), [carrinho])
        self.assertIsNone(store.obter("Mix de pimentas"))

    def test_baixa_apenas_compromissos_identificados_e_preserva_gasto_apos_remover_prato(self):
        store = Store()
        prato_id = store.salvar_prato("Frango", None, 4, 16, 4.0, {})
        store.aceitar_prato(prato_id, 8.0, [{"ingrediente": "Mix de pimentas", "quantidade": 50, "unidade": "g",
                                           "preco_estimado": 0.50}])
        orcamento = store.orcamento()
        id_compra = orcamento["compras_confirmadas"][0]["id"]
        carrinho = store.carrinho_adicionar("Tempero para aves", 0.75, 50, "g")
        compra = {"nome": "Mix de pimentas", "quantidade": 0.05, "unidade": "kg", "preco_total": 0.60}
        with self.assertRaisesRegex(ValueError, f"compra_id={id_compra}"):
            store.registrar_compras([compra])
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            store.registrar_compras([{**compra, "quantidade": 0.06, "compra_id": id_compra}])
        store.registrar_compras([
            {**compra, "compra_id": id_compra},
            {"nome": "Tempero para aves", "quantidade": 0.05, "unidade": "kg", "preco_total": 0.80,
             "carrinho_id": carrinho["id"]},
        ])
        self.assertEqual(store.orcamento()["saldo"], 78.60)
        self.assertEqual(store.carrinho_itens(), [])
        store.remover_prato(prato_id)
        self.assertEqual(store.orcamento()["saldo"], 78.60)
        self.assertEqual(len(store.orcamento()["compras_confirmadas"]), 2)

    def test_compra_da_receita_baixa_apenas_suas_linhas_e_pode_ser_repetida(self):
        store = Store()
        receita_id = store.salvar_receita("Frango", None, 4, {}, "viavel")
        outra_id = store.salvar_receita("Frango com legumes", None, 4, {}, "viavel")
        compra = {"ingrediente": "Mix de pimentas", "quantidade": 50, "unidade": "g", "preco_estimado": 0.50}
        store.carrinho_montar_da_receita(receita_id, "Frango", [compra])
        store.carrinho_montar_da_receita(outra_id, "Frango com legumes", [compra])
        confirmadas = [{"nome": "Mix de pimentas", "quantidade": 0.05, "unidade": "kg", "preco_total": 0.60}]
        lote = store.registrar_compras(confirmadas, chave="receita:confirmada", receita_id=receita_id)
        self.assertEqual(lote["orcamento"]["saldo"], 78.90)
        self.assertEqual([item["receita_id"] for item in store.carrinho_itens()], [outra_id])
        retry = store.registrar_compras(confirmadas, chave="receita:confirmada", receita_id=receita_id)
        self.assertTrue(retry["ja_registrado"])
        self.assertEqual(retry["orcamento"]["saldo"], 78.90)
        self.assertEqual(retry["ingredientes"][0]["estoque_base"], 0.05)

    def test_escolhas_persistem_sem_cobrar_ou_reservar_e_nao_duplicam(self):
        store = Store()
        escolhas = store.selecionar_pratos([
            {"nome": "Filé de Peito Grelhado com Arroz Branco e Feijão Carioca"},
            {"nome": "Almoço Prático: Arroz, Feijão e Frango Grelhado"},
        ])
        outro = Store()
        self.assertEqual(outro.pratos_em_andamento(), escolhas)
        repetidas = outro.selecionar_pratos([
            {"nome": "almoco pratico: arroz, feijao e frango grelhado"},
            {"nome": "file de peito grelhado com arroz branco e feijao carioca"},
        ])
        self.assertEqual([p["id"] for p in repetidas], [p["id"] for p in reversed(escolhas)])
        self.assertTrue(all(p["status"] == "selecionado" and p["cmv_total"] is None for p in escolhas))
        self.assertEqual(outro.reservas_ativas(), [])
        self.assertEqual(outro.orcamento()["saldo"], 80)

    def test_conferencia_e_cmv_avancam_mesmo_prato_e_invalidam_versao_antiga(self):
        store = Store()
        nome = "Frango grelhado"
        escolhido, = store.selecionar_pratos([{"nome": nome, "fonte_url": "https://exemplo.test/frango"}])
        prato_id = escolhido["id"]
        bloqueios = [{"tipo": "ingredientes", "detalhe": "Confirme a quantidade de arroz."}]
        pendente_id = store.salvar_receita("Frango grelhado da casa", None, None,
                                         {"veredito": "precisa_confirmar", "bloqueios": bloqueios,
                                          "resultado": {"pendencias": ["arroz"], "compras_total_estimado": 0}},
                                         "conferida", prato_id=prato_id)
        pendente, = store.pratos_em_andamento()
        self.assertEqual((pendente["id"], pendente["receita_id"], pendente["status"], pendente["bloqueios"]),
                         (prato_id, pendente_id, "selecionado", bloqueios))
        self.assertIsNone(pendente["compras_estimadas"])
        self.assertEqual(pendente["fonte_url"], "https://exemplo.test/frango")
        with self.assertRaises(ValueError):
            store.aceitar_prato(prato_id, 14, [])

        dados = {"resultado": {"compras_total_estimado": 3, "custo_vindo_da_despensa": 7}, "veredito": "viavel"}
        pronta_id = store.salvar_receita(nome, None, 2, dados, "conferida", prato_id=prato_id)
        self.assertEqual(store.salvar_prato(nome, pronta_id, 2, 10, 5, dados), prato_id)
        calculado, = store.pratos_em_andamento()
        self.assertEqual((calculado["nome"], calculado["status"], calculado["cmv_total"], calculado["compras_estimadas"],
                          calculado["custo_da_despensa"]), (nome, "cmv_calculado", 10, 3, 7))
        store.salvar_analise_preco(prato_id, {"teste": 1})
        self.assertEqual(store.salvar_prato(nome, pronta_id, 2, 10, 5, dados), prato_id)
        self.assertEqual(len(store.analises_preco()), 1)
        revisao = store.salvar_receita(nome, None, 3, dados, "conferida", prato_id=prato_id)
        self.assertEqual(store.analises_preco(), [])
        self.assertIsNone(store.obter_prato(prato_id)["cmv_total"])
        self.assertEqual(store.obter_prato(prato_id)["receita_id"], revisao)
        with self.assertRaisesRegex(ValueError, "mais recente"):
            store.salvar_prato(nome, pronta_id, 2, 10, 5, dados)

    def test_orcamento_separa_compras_pagas_cardapio_e_carrinho(self):
        store = Store()
        store.registrar_compras([{"nome": "Tempero para aves", "quantidade": 50, "unidade": "g", "preco_total": 1.25}])
        prato_id = store.salvar_prato("Assado", None, 2, 20, 10, {})
        store.aceitar_prato(prato_id, 15, [{"ingrediente": "Couve", "quantidade": 1, "unidade": "kg", "preco_estimado": 8}])
        store.carrinho_adicionar("Batata", 4)
        orcamento = store.orcamento()
        resumo = store.carrinho_resumo(orcamento=orcamento)
        self.assertEqual((resumo["gasto_confirmado"], resumo["reservado_cardapio"], resumo["carrinho"],
                          resumo["comprometido"], resumo["saldo"]), (1.25, 8, 4, 9.25, 66.75))
        store.remover_prato(prato_id)
        resumo = store.carrinho_resumo()
        self.assertEqual((resumo["gasto_confirmado"], resumo["reservado_cardapio"], resumo["saldo"]), (1.25, 0, 74.75))

    def test_substitui_rascunho_atomicamente_e_preserva_carrinho_e_orcamento(self):
        store = Store()
        original, = store.selecionar_pratos([{"nome": "Frango com arroz e feijão"}])
        prato_id = original["id"]
        store.salvar_prato(original["nome"], None, 2, 10, 5, {}, prato_id=prato_id)
        store.salvar_analise_preco(prato_id, {"preco": 12})
        store.carrinho_adicionar("Batata", 4)
        carrinho_antes = store.carrinho_itens()
        saldo_antes = store.orcamento()

        with self.assertRaisesRegex(ValueError, "diferente do original"):
            store.selecionar_pratos([{"nome": "Frango com arroz e feijao"}], substituir_prato_id=prato_id)
        self.assertEqual(len(store.analises_preco()), 1)
        self.assertEqual(store.obter_prato(prato_id)["status"], "cmv_calculado")

        alternativa, = store.selecionar_pratos([{"nome": "Frango com arroz"}], substituir_prato_id=prato_id)
        self.assertEqual(store.pratos_em_andamento(), [alternativa])
        removido = store.obter_prato(prato_id)
        self.assertEqual(removido["status"], "removido")
        self.assertEqual(removido["dados"]["remocao"]["substituido_por"], alternativa["id"])
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.carrinho_itens(), carrinho_antes)
        self.assertEqual(store.orcamento(), saldo_antes)

        self.assertEqual(store.selecionar_pratos([{"nome": "Frango com arroz"}], substituir_prato_id=prato_id), [alternativa])
        with self.assertRaisesRegex(ValueError, "já saiu da preparação"):
            store.selecionar_pratos([{"nome": "Frango assado"}], substituir_prato_id=prato_id)
        self.assertEqual(store.pratos_em_andamento(), [alternativa])

        store.salvar_prato(alternativa["nome"], None, 2, 10, 5, {}, prato_id=alternativa["id"])
        store.aceitar_prato(alternativa["id"], 12, [])
        with self.assertRaisesRegex(ValueError, "um prato ainda em preparação"):
            store.selecionar_pratos([{"nome": "Frango assado"}], substituir_prato_id=alternativa["id"])
        self.assertEqual([p["status"] for p in store.pratos_em_andamento()], ["aceito"])

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
