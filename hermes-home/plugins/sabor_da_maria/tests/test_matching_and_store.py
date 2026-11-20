import json
import unittest
from copy import deepcopy

from sabor_da_maria import cozinha, montagem, painel, tools
from sabor_da_maria.matching import best_matches, preparo_caseiro_entre, score
from sabor_da_maria.units import materia_prima_do_preparo
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

    def test_fresco_nao_vira_produto_pronto(self):
        self.assertEqual(best_matches("molho de tomate", ["Tomate"]), [])
        self.assertEqual(best_matches("batata palha", ["Batata"]), [])
        self.assertEqual(best_matches("molho de tomate", ["Tomate", "Molho de tomate pronto"])[0]["nome"],
                         "Molho de tomate pronto")


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

    def _prato_calculado(self, store, nome):
        prato, = store.selecionar_pratos([{"nome": nome}])
        receita = store.salvar_receita(nome, None, 2, {}, "conferida", prato_id=prato["id"])
        store.salvar_prato(nome, receita, 2, 10, 5, {}, prato_id=prato["id"])
        return prato["id"], receita

    def test_aceite_separa_carrinho_e_estoque_antes_do_preco_sem_debitar_ao_publicar(self):
        store = Store()
        peito = store.obter("Peito de frango")
        prato_id, receita_id = self._prato_calculado(store, "Frango cremoso")
        compras = [{"ingrediente": "Creme de leite", "quantidade": 200, "unidade": "g", "preco_estimado": 4}]
        da_despensa = [{"ingrediente": "Peito de frango", "quantidade": 500, "unidade": "g", "valor": 7}]
        reservas = [{"ingrediente_id": peito["id"], "quantidade_base": .5}]
        store.carrinho_montar_da_receita(receita_id, "Frango cremoso", compras)  # lista manual já existente

        def aceitar():
            store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id, compras=compras,
                                      da_despensa=da_despensa, reservas=reservas)

        aceitar()
        itens = store.carrinho_itens()
        self.assertEqual([(i["nome"], i["ja_tem"]) for i in itens], [("Peito de frango", True), ("Creme de leite", False)])
        self.assertEqual(store.obter_prato(prato_id)["status"], "cmv_calculado")
        self.assertEqual(store.obter("Peito de frango")["disponivel_base"], 1.5)
        self.assertEqual(store.carrinho_resumo()["saldo"], 76)
        self.assertEqual(store.orcamento()["compras_confirmadas"], [])
        avaliador = Store(ignorar_prato_id=prato_id)
        self.assertEqual(avaliador.carrinho_resumo()["saldo"], 80)
        self.assertEqual(avaliador.obter("Peito de frango")["disponivel_base"], 2)

        avulso = store.carrinho_adicionar("Outro item", 3)
        aceitar()
        self.assertEqual(store.carrinho_itens(), [*itens, avulso])
        for baixados in (2, 0):
            publicado = store.aceitar_prato(prato_id, 14, compras, reservas, receita_id_esperada=receita_id)
            self.assertEqual(publicado["linhas_do_carrinho_que_viraram_compras"], baixados)
            resumo = store.carrinho_resumo()
            self.assertEqual((resumo["saldo"], resumo["reservado_cardapio"], resumo["gasto_confirmado"]), (73, 4, 0))
            self.assertEqual(store.carrinho_itens(), [avulso])
            self.assertEqual(store.obter("Peito de frango")["disponivel_base"], 1.5)

    def test_compromisso_sem_saldo_ou_estoque_reverte_gravacao_e_revisao_libera(self):
        store = Store()
        peito = store.obter("Peito de frango")
        prato_id, receita_id = self._prato_calculado(store, "Frango com cebola")
        outro_id, outra_receita = self._prato_calculado(store, "Frango com alho")
        reservas = [{"ingrediente_id": peito["id"], "quantidade_base": 1.5}]
        compras = [{"ingrediente": "Orégano", "quantidade": 30, "unidade": "g", "preco_estimado": 10}]
        avulso = store.carrinho_adicionar("Pacote avulso", 75)

        with self.assertRaises(OrcamentoEstourado):
            store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id, compras=compras, reservas=reservas)
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [avulso])
        store.carrinho_remover(avulso["id"])

        store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id, compras=compras, reservas=reservas)
        with self.assertRaisesRegex(ValueError, "estoque disponível"):
            store.salvar_analise_preco(outro_id, {"cenarios": []}, receita_id_esperada=outra_receita, compras=compras, reservas=reservas)
        self.assertEqual([a["prato_id"] for a in store.analises_preco()], [prato_id])
        self.assertEqual(store.carrinho_resumo()["saldo"], 70)

        store.salvar_receita("Frango com cebola", None, 2, {}, "conferida", prato_id=prato_id)
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.carrinho_resumo()["saldo"], 80)
        store.salvar_analise_preco(outro_id, {"cenarios": []}, receita_id_esperada=outra_receita, compras=compras, reservas=reservas)
        store.selecionar_pratos([{"nome": "Nova alternativa"}], substituir_prato_id=outro_id)
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.carrinho_resumo()["saldo"], 80)

    def test_liberar_compromisso_inviavel_preserva_a_ficha_e_a_previa_sem_compromisso(self):
        store = Store()
        peito = store.obter("Peito de frango")
        prato_id, receita_id = self._prato_calculado(store, "Frango com orégano")
        self.assertFalse(store.liberar_compromisso_inviavel(prato_id, receita_id))
        self.assertEqual(store.obter_prato(prato_id)["status"], "cmv_calculado")

        store.salvar_analise_preco(
            prato_id, {"cenarios": []}, receita_id_esperada=receita_id,
            compras=[{"ingrediente": "Orégano", "quantidade": 15, "unidade": "g", "preco_estimado": 5}],
            reservas=[{"ingrediente_id": peito["id"], "quantidade_base": 1}],
        )
        self.assertFalse(store.liberar_compromisso_inviavel(prato_id, receita_id + 1))
        self.assertEqual(store.carrinho_resumo()["saldo"], 75)
        self.assertTrue(store.liberar_compromisso_inviavel(prato_id, receita_id))
        self.assertFalse(store.liberar_compromisso_inviavel(prato_id, receita_id))
        prato = store.obter_prato(prato_id)
        self.assertEqual((prato["receita_id"], prato["status"], prato["cmv_total"]), (receita_id, "selecionado", None))
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_resumo()["saldo"], 80)

    def test_remover_grupo_automatico_libera_lote_e_desfaz_intencao_persistida(self):
        store = Store()
        peito = store.obter("Peito de frango")
        nome, fonte = "Frango ao forno", "https://exemplo.test/frango-ao-forno"
        prato, = store.selecionar_pratos([{"nome": nome, "fonte_url": fonte, "usar_quando_viavel": True}])
        prato_id = prato["id"]
        receita_id = store.salvar_receita(nome, fonte, 2, {}, "conferida", prato_id=prato_id)
        store.salvar_prato(nome, receita_id, 2, 10, 5, {}, prato_id=prato_id)
        store.salvar_analise_preco(
            prato_id, {"cenarios": []}, receita_id_esperada=receita_id,
            da_despensa=[{"ingrediente": peito["nome"], "quantidade": 500, "unidade": "g", "valor": 7}],
            reservas=[{"ingrediente_id": peito["id"], "quantidade_base": .5}],
        )
        avulso = store.carrinho_adicionar("Alcaparras", 2)
        self.assertTrue(store.obter_prato(prato_id)["dados"]["selecao"]["usar_quando_viavel"])
        self.assertEqual(len(store.carrinho_remover_prato(nome)), 1)
        reaberto = store.obter_prato(prato_id)
        self.assertEqual((reaberto["receita_id"], reaberto["status"]), (receita_id, "selecionado"))
        self.assertEqual(reaberto["dados"]["selecao"], {"fonte_url": fonte})
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [avulso])

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


class PreparosCaseirosTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.molho = {"nome": "molho de tomate quanto baste", "quantidade": 150, "unidade": "g"}

    def resolver(self, *ingredientes):
        return tools._resolver_ingredientes(self.store, list(ingredientes) or [self.molho])

    def perfil_completo(self):
        self.store.perfil_salvar({
            "equipamentos": {i.id: 4 if i.id == "fogao" else True for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE},
            "tecnicas": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
            "restricoes": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120,
                           "gas": "botijão", "energia": "estável"},
        })

    def ficha(self, nome="Pizza de frango", usar=False):
        fonte = {"nome": nome, "url": "https://exemplo.test/pizza", "porcoes": 4,
                 "tempo_preparo_min": 30, "ingredientes": ["150 g de molho de tomate quanto baste"],
                 "modo_preparo": "Coloque o molho na panela e aqueça no fogão."}
        prato, = self.store.selecionar_pratos([{"nome": nome, "fonte_url": fonte["url"], "usar_quando_viavel": usar}])
        rid = self.store.salvar_receita(nome, fonte["url"], 4, {
            "fonte_verificada": fonte, "modo_preparo": fonte["modo_preparo"], "ingredientes": [dict(self.molho)],
            "tempo_preparo_min": 30, "sessao_da_conferencia": "derivados",
        }, "conferida", prato_id=prato["id"])
        return self.store.obter_prato(prato["id"]), self.store.obter_receita(rid)

    def test_molho_usa_tomate_sem_compra_e_sem_mutacao(self):
        antes = self.store.obter("Tomate")
        res = self.resolver()
        self.assertEqual(res["pendencias"], [])
        self.assertEqual(res["compras"], [])
        linha, = res["itens"]
        self.assertEqual(linha["item_despensa"], "Tomate")
        self.assertAlmostEqual(linha["usa_da_despensa_base"], .1875)
        self.assertEqual(linha["custo_uso_total"], round(.1875 * antes["custo_unitario_base"], 2))
        self.assertIn("rendimento local estimado", linha["conversao"])
        self.assertEqual(self.store.obter("Tomate"), antes)
        self.assertIsNone(self.store.obter("molho de tomate"))
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_outros_derivados_aplicam_rendimento(self):
        for nome, materia, pronto, cru in (("purê de batatas", "Batata", 170, .2),
                                          ("arroz branco cozido", "Arroz branco tipo 1", 500, .2),
                                          ("feijão carioca cozido", "Feijão carioquinha", 400, .2),
                                          ("feijão preto cozido", "Feijão preto", 400, .2),
                                          ("peito de frango cozido e desfiado", "Peito de frango", 150, .2)):
            with self.subTest(nome=nome):
                res = self.resolver({"nome": nome, "quantidade": pronto, "unidade": "g"})
                self.assertEqual(res["pendencias"], [])
                self.assertEqual(res["itens"][0]["item_despensa"], materia)
                self.assertAlmostEqual(res["itens"][0]["usa_da_despensa_base"], cru)

    def test_plurais_caseiro_e_direcao(self):
        for nome in ("molho caseiro de tomates", "passata de tomate caseira", "purê de tomates a gosto"):
            self.assertIsNotNone(preparo_caseiro_entre(nome, "Tomate"))
        for nome, candidato in (("Tomate", "molho de tomate"), ("extrato de tomate", "Tomate"),
                                ("molho de tomate", "Tomate seco"), ("leite condensado", "Leite integral"),
                                ("frango desfiado", "Coxa de frango"), ("arroz cozido", "Arroz cozido")):
            self.assertIsNone(preparo_caseiro_entre(nome, candidato))

    def test_volume_usa_densidade_do_pronto(self):
        res = self.resolver({**self.molho, "quantidade": 1, "unidade": "xícara"})
        self.assertAlmostEqual(res["itens"][0]["usa_da_despensa_base"], .3)
        conv = materia_prima_do_preparo(1, "xícara", "feijão preto cozido", "Feijão preto", "kg", 2)
        self.assertAlmostEqual(conv.qty_base, .09)

    def test_unidade_de_embalagem_nao_vira_um_tomate(self):
        res = self.resolver({**self.molho, "quantidade": 1, "unidade": "un"})
        self.assertIn("peso/volume", res["pendencias"][0])
        self.assertNotIn("item_id", res["itens"][0])
        for rendimento in (0, -1, float("nan"), float("inf")):
            self.assertIsNone(materia_prima_do_preparo(150, "g", "molho de tomate", "Tomate", "kg", rendimento).qty_base)

    def test_produto_pronto_disponivel_tem_preferencia(self):
        self.store.registrar_compra("Molho de tomate pronto", 1, "kg", 12, novo=True)
        res = self.resolver()
        self.assertEqual(res["preparos_caseiros"], [])
        self.assertEqual(res["itens"][0]["item_despensa"], "Molho de tomate pronto")
        self.assertAlmostEqual(res["itens"][0]["usa_da_despensa_base"], .15)

    def test_vinculo_explicito_a_materia_prima_e_aceito(self):
        res = self.resolver({**self.molho, "item_despensa": "Tomate"})
        self.assertEqual(res["pendencias"], [])
        res = self.resolver({**self.molho, "item_despensa": "Batata"})
        self.assertTrue(res["pendencias"])

    def test_compra_explicita_de_pronto_e_preservada(self):
        cotacao = {"preco_compra_estimado": 10, "quantidade_compra": 1, "unidade_compra": "kg"}
        res = self.resolver({**self.molho, "item_despensa": None, **cotacao})
        self.assertEqual(res["preparos_caseiros"], [])
        self.assertEqual(res["compras_total_estimado"], 10)
        self.assertEqual(res["cmv_total"], 1.5)

    def test_estoque_compartilhado_no_lote_nao_e_contado_duas_vezes(self):
        estoque = self.store.obter("Tomate")["estoque_base"]
        direto = {"nome": "Tomate", "quantidade": estoque - .1, "unidade": "kg"}
        for ingredientes in ((direto, self.molho), (self.molho, direto)):
            res = self.resolver(*ingredientes)
            self.assertEqual(res["pendencias"], [])
            self.assertAlmostEqual(sum(i["usa_da_despensa_base"] for i in res["itens"]), estoque)
            self.assertEqual(res["compras"][0]["ingrediente"], "Tomate")
            self.assertAlmostEqual(res["compras"][0]["quantidade"], .0875)
        repetido = self.resolver(self.molho, dict(self.molho))
        self.assertEqual(len(repetido["itens"]), 1)
        self.assertTrue(repetido["pendencias"])

    def test_compra_de_tomate_nao_usa_cotacao_do_molho(self):
        estoque = self.store.obter("Tomate")["estoque_base"]
        res = self.resolver({**self.molho, "quantidade": (estoque + .2) * .8, "unidade": "kg",
                             "preco_compra_estimado": 100, "quantidade_compra": 1, "unidade_compra": "kg"})
        self.assertAlmostEqual(res["compras"][0]["quantidade"], .2)
        self.assertEqual(res["compras"][0]["ingrediente"], "Tomate")
        self.assertLess(res["compras"][0]["preco_estimado"], 100)

    def test_duas_faltas_da_mesma_materia_prima_sao_somadas(self):
        estoque = self.store.obter("Tomate")["estoque_base"]
        res = self.resolver({"nome": "Tomate", "quantidade": estoque + .1, "unidade": "kg"}, self.molho)
        self.assertEqual(len(res["compras"]), 1)
        self.assertAlmostEqual(res["compras"][0]["quantidade"], .2875)
        self.assertEqual(sum(c["preco_estimado"] for c in res["compras"]), res["compras_total_estimado"])

    def test_agua_simples_e_utilidade_mas_nao_agua_mineral(self):
        for nome in ("água", "água morna", "água potável filtrada"):
            res = self.resolver({"nome": nome, "quantidade": 100, "unidade": "ml"})
            self.assertEqual(res["pendencias"], [])
            self.assertEqual(res["cmv_total"], 0)
            self.assertEqual(res["compras"], [])
        self.assertTrue(self.resolver({"nome": "água mineral", "quantidade": 100, "unidade": "ml"})["pendencias"])

    def test_fonte_preservada_tempo_extra_e_reconferencia_idempotente(self):
        self.perfil_completo()
        prato, receita = self.ficha()
        antes = deepcopy(receita)
        for _ in range(2):
            aval = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
            self.assertEqual(aval["bloqueios"], [])
            self.assertEqual(aval["requisitos"]["tempo_preparo_min"], 50)
            self.assertEqual(aval["requisitos"]["tempo_receita_min"], 30)
        self.assertEqual(receita, antes)
        self.assertEqual(self.store.obter_receita(receita["id"]), antes)
        self.assertEqual(self.store.obter_prato(prato["id"])["status"], "selecionado")

    def test_molho_simples_nao_exige_panela_especial(self):
        self.perfil_completo()
        _, receita = self.ficha()
        self.store.perfil_salvar({"equipamentos": {"panela_grande": False}})
        aval = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
        self.assertEqual(aval["bloqueios"], [])
        self.assertNotIn("panela grande", aval["requisitos"]["equipamentos_necessarios"])

    def test_tool_reconfere_sem_acumular_tempo_e_sem_compromissos(self):
        self.perfil_completo()
        prato, receita = self.ficha()
        original = deepcopy(receita["dados"]["fonte_verificada"])
        for _ in range(2):
            saida = json.loads(tools.receita_conferir_ingredientes({"prato_id": prato["id"],
                                "ingredientes": [dict(self.molho)], "porcoes": 4}, session_id="derivados"))
            self.assertTrue(saida["success"], saida)
            self.assertEqual(saida["veredito"], "viavel")
            self.assertEqual(saida["tempo_preparo_min"], 50)
            self.assertEqual(saida["tempo_receita_min"], 30)
            self.assertIn("Preparos caseiros locais", saida["diga_a_dona_maria"])
            salva = self.store.obter_receita(saida["receita_id"])
            self.assertEqual(salva["dados"]["fonte_verificada"], original)
            self.assertEqual(salva["dados"]["tempo_preparo_min"], 30)
            self.assertEqual(self.store.reservas_ativas(), [])
            self.assertEqual(self.store.carrinho_itens(), [])
            self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_gas_tempo_e_equipamento_continuam_conferidos(self):
        self.perfil_completo()
        _, receita = self.ficha()
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 40, "gas": "sem gás"},
                                 "equipamentos": {"fogao": 0, "fogão a gás": 0}})
        aval = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
        tipos = {b["tipo"] for b in aval["bloqueios"]}
        self.assertIn("tempo_insuficiente", tipos)
        self.assertIn("gas_indisponivel", tipos)
        self.assertIn("equipamento_faltando", tipos)
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_retomada_apresenta_preparo_antes_do_aceite(self):
        self.perfil_completo()
        prato, receita = self.ficha()
        montagem.iniciar_turno("derivados", "retomada")
        painel._pergunta_da_retomada(self.store, prato, receita, "derivados")
        texto = montagem.resposta_preparada("derivados", "retomada")
        self.assertIn("Preparos caseiros locais", texto)
        self.assertIn("Tomate", texto)
        self.assertIn("aceita preparar", texto)
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_selecao_antiga_nao_aceita_derivado_automaticamente(self):
        self.perfil_completo()
        prato, receita = self.ficha(usar=True)
        self.assertIsNone(tools._concluir_escolha_viavel(self.store, "derivados", prato["id"]))
        self.assertEqual(self.store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertEqual(self.store.analises_preco(), [])

    def test_reserva_usa_materia_prima_entre_pratos_sem_novo_debito(self):
        res = self.resolver()
        prato, receita = self.ficha()
        self.store.salvar_prato(prato["nome"], receita["id"], 4, res["cmv_total"], res["cmv_total"] / 4,
                                {"resultado": res}, prato_id=prato["id"])
        reservas = tools._reservas_de(res)
        carrinho = tools._itens_da_despensa_para_carrinho(res)
        self.assertEqual(carrinho[0]["ingrediente"], "Tomate")
        for _ in range(2):
            self.store.salvar_analise_preco(prato["id"], {"cenarios": []}, receita_id_esperada=receita["id"],
                                          da_despensa=carrinho, reservas=reservas, compras=[])
        item = self.store.obter("Tomate")
        self.assertAlmostEqual(item["reservado_base"], .1875)
        segundo = self.resolver({**self.molho, "quantidade": item["estoque_base"] * .8, "unidade": "kg"})
        self.assertAlmostEqual(segundo["compras"][0]["quantidade"], .1875)
        self.assertEqual(self.store.orcamento()["saldo"], 80)
        self.assertEqual(self.store.orcamento()["compras_confirmadas"], [])


if __name__ == "__main__":
    unittest.main()
