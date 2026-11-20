import json
import unittest

from sabor_da_maria import api, costing, falas, taxas, tools
from sabor_da_maria.store import Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


class CostingTests(unittest.TestCase):
    def test_regra_do_desafio_prevalece_sobre_perfis_legados_e_taxas_avulsas(self):
        for dados in ({}, {"plano": "basico"}, {"plano": "entrega", "imposto": 0.04},
                      {"plano": "personalizado", "comissao": 0.30, "pagamento": 0.032, "imposto": 0.10}):
            with self.subTest(dados=dados):
                perfil = taxas.normalizar(dados)
                self.assertEqual(perfil, {"plano": "desafio", "comissao": 0.10, "pagamento": 0.0,
                                          "pagamento_online": False, "imposto": 0.0})
                aval = costing.avaliar_preco(100, 20, taxa=0.85, perfil=dados)
                self.assertEqual([aval[k] for k in ("taxa_comissao", "taxa_pagamento", "impostos", "repasse_ifood", "recebe_liquido", "lucro")],
                                 [10, 0, 0, 90, 90, 70])
                self.assertEqual(costing.cenarios_preco(9, taxa=0.85, perfil=dados)["preco_minimo"], 10)
        self.assertEqual(costing.avaliar_preco(100, 20, taxa=0.85)["lucro"], 70)
        self.assertEqual(costing.preco_minimo(9, taxa=0.85), 10)

    def test_minimo_respeita_cmv_dividido_por_noventa_por_cento_antes_de_arredondar(self):
        for perfil in (None, taxas.normalizar({"plano": "entrega"})):
            with self.subTest(perfil=perfil):
                self.assertEqual(costing.preco_minimo_centavo(10, perfil=perfil), 11.12)
                self.assertFalse(costing.avaliar_preco(11.11, 10, perfil=perfil)["viavel"])
                self.assertTrue(costing.avaliar_preco(11.12, 10, perfil=perfil)["viavel"])
                self.assertEqual(costing.preco_minimo_centavo(7 / 3, perfil=perfil), 2.60)
                self.assertEqual(costing.cenarios_preco(7 / 3, perfil=perfil)["preco_minimo"], 2.60)
                minimo = costing.cenarios_preco(20, perfil=perfil)
                self.assertFalse(costing.avaliar_preco(minimo["preco_minimo"] - 0.01, 20, perfil=perfil)["viavel"])
                self.assertGreater(costing.avaliar_preco(minimo["preco_minimo_com_sobra"], 20, perfil=perfil)["lucro"], 0)

    def test_repasse_e_lucro_usam_formula_exata_antes_do_arredondamento(self):
        aval = costing.avaliar_preco(11.15, 10)
        self.assertEqual(aval["recebe_liquido"], 10.04)
        self.assertEqual(aval["lucro"], 0.04)
        self.assertIn("≈", aval["explicacao"])
        self.assertIn("arredondados ao centavo", aval["explicacao"])

    def test_cmv_fracionado_so_e_arredondado_para_exibir(self):
        # Lote de 6,01 em duas porções: 3,005 por porção. O lucro é
        # 0,90 × 5 − 3,005 = 1,495, exibido como 1,50.
        cmv = 6.01 / 2
        analise = costing.cenarios_preco(cmv)
        aval = costing.avaliar_preco(5, cmv)
        self.assertEqual((analise["cmv_por_porcao"], aval["cmv"]), (3.01, 3.01))
        self.assertEqual(aval["lucro"], 1.50)
        self.assertEqual(costing.brl(cmv), "R$ 3,01")
        self.assertEqual(costing.brl(-cmv), "-R$ 3,01")
        self.assertEqual(analise["preco_minimo"], 3.34)

    def test_divisao_do_lote_nao_aumenta_o_minimo_por_ruido_binario(self):
        for total, porcoes, cmv, minimo in ((4.32, 3, 1.44, 1.60), (5.94, 3, 1.98, 2.20), (5.94, 5, 1.188, 1.32)):
            with self.subTest(total=total, porcoes=porcoes):
                custo = costing.custo_por_porcao(total, porcoes)
                self.assertEqual(custo, cmv)
                self.assertEqual(costing.preco_minimo_centavo(custo), minimo)
                self.assertTrue(costing.avaliar_preco(minimo, custo)["viavel"])

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

    def test_cenarios_oferecem_duas_ou_tres_opcoes_de_margem(self):
        self.assertEqual(len(costing.cenarios_preco(9, margens=[0.2, 0.4])["cenarios"]), 2)
        for margens in ([0.2], [0.1, 0.2, 0.3, 0.4], [0.2, 1.0], [0.2, float("nan")]):
            with self.subTest(margens=margens), self.assertRaises(ValueError):
                costing.cenarios_preco(9, margens=margens)

    def test_brl_and_rounding(self):
        self.assertEqual(costing.brl(1234.5), "R$ 1.234,50")
        self.assertEqual(costing.brl(-3.1), "-R$ 3,10")
        self.assertEqual(costing.round_up_to(23.1, 0.5), 23.5)
        self.assertEqual(costing.round_up_to(23.5, 0.5), 23.5)


def call(name, **args):
    return json.loads(tools.HANDLERS[name](args, session_id=SESSAO))


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
        diz(FALA_COZINHA)
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

        # Trava: com veredito inviavel/precisa_confirmar o backend recusa o CMV e nada é gravado.
        bloqueado = call("prato_calcular_cmv", nome="Frango com batatas", porcoes=4, receita_id=receita["receita_id"])
        self.assertFalse(bloqueado["success"])
        self.assertTrue(bloqueado["bloqueado"])
        self.assertEqual({b["tipo"] for b in bloqueado["bloqueios"]}, {"tecnica_faltando", "equipamento_nao_informado", "tecnica_nao_informada"})
        self.assertEqual(call("cardapio_consultar")["pratos_em_analise"], [])

        # Dona Maria responde o que faltava; o CMV reavalia sozinho, sem conferir a receita de novo.
        diz(FALA_COZINHA, "Também tenho batedeira. Agora sei fazer massa fresca e o ponto da carne.")
        call("perfil_cozinha_salvar", equipamentos={"batedeira": True}, tecnicas={"massa fresca": "sim", "ponto de carne": "sim"})
        cmv = call("prato_calcular_cmv", nome="Frango com batatas", porcoes=4, receita_id=receita["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        self.assertEqual(cmv["veredito"], "viavel")
        esperado = 14.0 + 6.0 + 0.6 + 0.3 + round(0.03 * 61.98, 4) + 3.5 + round(0.005 * 1.85, 4)
        self.assertAlmostEqual(cmv["cmv_total"], round(esperado, 2), places=1)
        self.assertAlmostEqual(cmv["cmv_por_porcao"], round(cmv["cmv_total"] / 4, 2))
        self.assertTrue(any("CMV por porção" in linha for linha in cmv["memoria_de_calculo"]))
        self.assertIn({"ingrediente": "Peito de frango", "quantidade": "1 kg"}, cmv["reservas_previstas"])

        preco = call("preco_sugerir", cmv_por_porcao=cmv["cmv_por_porcao"], prato_id=cmv["prato_id"], precos_para_simular=[15, 25])
        self.assertEqual(len(preco["cenarios"]), 3)
        self.assertEqual(len(preco["simulacoes"]), 2)
        self.assertEqual(preco["preco_minimo"], 7.30)
        self.assertEqual((preco["taxa_comissao"], preco["taxa_pagamento"]), (0.10, 0.0))

        escolhido = preco["cenarios"][1]["preco_sugerido"]
        registro = call("cardapio_registrar", prato_id=cmv["prato_id"], preco_escolhido=escolhido)
        self.assertTrue(registro["success"], registro)
        self.assertFalse(registro["ja_registrado"])
        self.assertEqual(registro["orcamento"]["comprometido"], 3.5)
        self.assertEqual(registro["orcamento"]["restante"], 76.5)
        self.assertIn({"ingrediente": "Peito de frango", "quantidade": "1 kg"}, registro["reservas_registradas"])

        # Retry: repetir o registro do mesmo prato não duplica orçamento nem reserva.
        de_novo = call("cardapio_registrar", prato_id=cmv["prato_id"], preco_escolhido=escolhido)
        self.assertTrue(de_novo["success"], de_novo)
        self.assertTrue(de_novo["ja_registrado"])
        self.assertEqual(de_novo["orcamento"]["comprometido"], 3.5)
        self.assertEqual(len(de_novo["reservas_registradas"]), len(registro["reservas_registradas"]))

        # A reserva aparece na despensa e já conta para o próximo prato.
        frango = call("despensa_buscar", ingredientes=["peito de frango"])["resultados"][0]["candidatos"][0]
        self.assertEqual((frango["estoque_legivel"], frango["disponivel_legivel"]), ("2 kg", "1 kg"))
        segundo = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Frango ao molho", porcoes=4,
                       ingredientes=[{"nome": "peito de frango", "quantidade": 1.5, "unidade": "kg"}])
        linha = segundo["ingredientes"][0]
        self.assertEqual((linha["tem_na_despensa"], linha["reservado_por_outros_pratos"], linha["falta"]), ("1 kg", "1 kg", "500 g"))

        cardapio = call("cardapio_consultar")
        self.assertEqual(len(cardapio["cardapio"]), 1)
        self.assertEqual(cardapio["cardapio"][0]["nome"], "Frango com batatas")
        self.assertGreater(cardapio["cardapio"][0]["lucro_por_porcao"], 0)
        self.assertEqual(cardapio["estoque_reservado"][0]["ingrediente"], "Peito de frango")

        # Remover libera a reserva e devolve o orçamento.
        removido = call("cardapio_remover", prato_id=cmv["prato_id"], motivo="mudou de ideia")
        self.assertTrue(removido["success"], removido)
        self.assertEqual(removido["orcamento"]["restante"], 80.0)
        self.assertIn({"ingrediente": "Peito de frango", "quantidade": "1 kg"}, removido["reservas_liberadas"])
        self.assertEqual(call("despensa_buscar", ingredientes=["peito de frango"])["resultados"][0]["candidatos"][0]["disponivel_legivel"], "2 kg")
        self.assertFalse(call("preco_sugerir", prato_id=cmv["prato_id"])["success"])
        self.assertEqual(call("cardapio_consultar")["cardapio"], [])

    def test_missing_quantities_and_unknown_content_become_pendencias(self):
        out = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Bolo", porcoes=8, ingredientes=[
            {"nome": "cobertura de chocolate", "quantidade": 200, "unidade": "g"},
            {"nome": "farinha de trigo", "quantidade": 0, "unidade": "g"},
            {"nome": "leite condensado", "quantidade": 1, "unidade": "lata"},
        ])
        self.assertEqual(out["veredito"], "precisa_confirmar")
        self.assertEqual(len(out["pendencias"]), 3)
        self.assertTrue(any("peso/volume" in p for p in out["pendencias"]))
        self.assertTrue(any("despensa_atualizar" in p for p in out["proximos_passos"]))
        self.assertFalse(any("despensa_atualizar" in p for p in out["pendencias"]))
        diz("O pote de cobertura de chocolate tem 1 kg.")
        call("despensa_atualizar", ingrediente="Cobertura de chocolate", conteudo_por_unidade={"quantidade": 1, "unidade": "kg"})
        out2 = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Bolo", porcoes=8, ingredientes=[
            {"nome": "cobertura de chocolate", "quantidade": 200, "unidade": "g"},
        ])
        cob = out2["ingredientes"][0]
        self.assertEqual(cob["custo_uso_total"], 15.98)  # 0,2 un × R$ 79,90

    def test_confirmacao_da_receita_lanca_so_complementos_e_atualiza_saldo_da_api(self):
        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Frango grelhado", porcoes=10, ingredientes=[
            {"nome": "Peito de frango", "quantidade": 1, "unidade": "kg"},
            {"nome": "Mix de pimentas", "quantidade": 50, "unidade": "g", "item_despensa": "",
             "quantidade_compra": 50, "unidade_compra": "g", "preco_compra_estimado": 0.50},
            {"nome": "Tempero para aves", "quantidade": 50, "unidade": "g", "item_despensa": "",
             "quantidade_compra": 50, "unidade_compra": "g", "preco_compra_estimado": 0.75},
        ])
        proposta = "\n".join(f"- {linha}" for linha in receita["compras_para_confirmar"])
        proposta += "\nPosso registrar essas compras na despensa?"
        historico = [{"role": "assistant", "content": proposta}]
        falas.registrar(SESSAO, historico, "Quanto vai sobrar se eu comprar?")
        self.assertFalse(call("despensa_registrar_compras", receita_id=receita["receita_id"])["success"])
        falas.registrar(SESSAO, historico, "Confirmo tudo isso, adicione ao meu estoque.")
        antes = Store().obter("Peito de frango")
        out = call("despensa_registrar_compras", receita_id=receita["receita_id"])
        self.assertTrue(out["success"], out)
        self.assertEqual(out["total"], 1.25)
        self.assertEqual([i["estoque_base"] for i in out["ingredientes"]], [0.05, 0.05])
        self.assertEqual(Store().obter("Peito de frango"), antes)
        self.assertEqual(api.tratar("GET", "/api/estado")[1]["orcamento"]["saldo"], 78.75)
        falas.registrar(SESSAO, historico, "Sim.")
        self.assertTrue(call("despensa_registrar_compras", receita_id=receita["receita_id"])["ja_registrado"])

    def test_compra_avulsa_exige_dados_da_fala_e_tool_antiga_nao_grava_parcial(self):
        itens = [{"nome": "Mix de pimentas", "quantidade": 50, "unidade": "g", "preco_total": 0.50},
                 {"nome": "Tempero para aves", "quantidade": 50, "unidade": "g", "preco_total": 0.75}]
        diz("Não comprei Mix de pimentas 50 g por R$ 0,50.")
        self.assertFalse(call("despensa_registrar_compras", compras=itens[:1])["success"])
        antes = Store().obter("Peito de frango")
        self.assertFalse(call("despensa_atualizar", ingrediente="Peito de frango", compra={"preco_total": 28},
                              conteudo_por_unidade={"quantidade": 2, "unidade": "kg"})["success"])
        self.assertEqual(Store().obter("Peito de frango"), antes)
        diz("Comprei Mix de pimentas 50 g por R$ 0,50 e Tempero para aves 50 g por R$ 0,75.")
        adulterados = [itens[0], {**itens[1], "preco_total": 0.50}]
        self.assertFalse(call("despensa_registrar_compras", compras=adulterados)["success"])
        self.assertIsNone(Store().obter("Mix de pimentas"))
        self.assertEqual(call("despensa_registrar_compras", compras=itens)["orcamento"]["saldo"], 78.75)

    def test_insufficient_stock_is_flagged_and_priced(self):
        out = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Strogonoff", porcoes=6, ingredientes=[
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


class GateTests(StoreTestCase):
    """As travas ficam no backend: o modelo não consegue chegar ao CMV, ao preço nem ao cardápio com pendência."""

    def _perfil_completo(self):
        diz(FALA_COZINHA)
        call("perfil_cozinha_salvar",
             equipamentos={"fogão": 4, "forno": True, "panela de pressão": True, "air fryer": False, "liquidificador": True},
             restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão ok", "energia": "estável"},
             tecnicas={"refogado": "sim"})

    def test_cmv_requires_conferred_recipe(self):
        self._perfil_completo()
        out = call("prato_calcular_cmv", nome="Arroz", porcoes=4, ingredientes=[{"nome": "arroz", "quantidade": 500, "unidade": "g"}])
        self.assertFalse(out["success"])
        self.assertEqual(out["bloqueios"][0]["tipo"], "sem_receita_conferida")
        self.assertEqual(call("cardapio_consultar")["pratos_em_analise"], [])

    def test_cmv_blocked_while_available_time_is_pending(self):
        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Arroz", porcoes=4, ingredientes=[{"nome": "arroz", "quantidade": 500, "unidade": "g"}])
        self.assertEqual(receita["veredito"], "precisa_confirmar")
        out = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual({b["tipo"] for b in out["bloqueios"]}, {"tempo_disponivel_pendente"})
        self.assertEqual(len(out["bloqueios"]), 1)
        self.assertTrue(any("operacionais" in p for p in out["proximos_passos"]))

    def test_missing_oven_blocks_cmv(self):
        self._perfil_completo()
        diz(FALA_COZINHA, "Agora não tenho forno.")
        call("perfil_cozinha_salvar", equipamentos={"forno": False})
        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Carne moída gratinada", porcoes=6,
                       ingredientes=[{"nome": "carne moída", "quantidade": 500, "unidade": "g"}],
                       equipamentos_necessarios=["forno"], tecnicas_necessarias=["refogado"])
        self.assertEqual(receita["veredito"], "inviavel_por_enquanto")
        out = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        self.assertFalse(out["success"])
        self.assertEqual([b["tipo"] for b in out["bloqueios"]], ["equipamento_faltando"])

    def test_over_budget_is_refused(self):
        self._perfil_completo()
        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Salmão", porcoes=2, ingredientes=[
            {"nome": "salmão", "quantidade": 1, "unidade": "kg", "item_despensa": "", "preco_compra_estimado": 95, "quantidade_compra": 1, "unidade_compra": "kg"}])
        self.assertEqual(receita["veredito"], "inviavel_por_enquanto")
        out = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        self.assertFalse(out["success"])
        self.assertEqual(out["bloqueios"][0]["tipo"], "orcamento")

    def test_preco_requires_prato_with_cmv(self):
        out = call("preco_sugerir", cmv_por_porcao=10)
        self.assertFalse(out["success"])
        self.assertEqual(out["bloqueios"][0]["tipo"], "sem_cmv_calculado")

    def test_registration_refuses_when_numbers_changed(self):
        self._perfil_completo()
        a = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Prato A", porcoes=4, ingredientes=[{"nome": "peito de frango", "quantidade": 1.5, "unidade": "kg"}])
        b = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Prato B", porcoes=4, ingredientes=[
            {"nome": "peito de frango", "quantidade": 1.5, "unidade": "kg", "preco_compra_estimado": 16, "quantidade_compra": 1, "unidade_compra": "kg"}])
        cmv_a = call("prato_calcular_cmv", receita_id=a["receita_id"])
        cmv_b = call("prato_calcular_cmv", receita_id=b["receita_id"])
        self.assertEqual((cmv_a["cmv_total"], cmv_b["cmv_total"]), (21.0, 21.0))  # candidatas não consomem estoque: as duas veem 2 kg
        self.assertTrue(call("cardapio_registrar", prato_id=cmv_a["prato_id"], preco_escolhido=12)["success"])
        # B foi apresentado usando 1,5 kg da despensa; com A aceito sobra 0,5 kg → compra de 1 kg e CMV diferente.
        reg_b = call("cardapio_registrar", prato_id=cmv_b["prato_id"], preco_escolhido=12)
        self.assertFalse(reg_b["success"])
        self.assertEqual(reg_b["bloqueios"][0]["tipo"], "numeros_mudaram")
        self.assertEqual(call("cardapio_consultar")["orcamento"]["comprometido"], 0.0)
        cmv_b2 = call("prato_calcular_cmv", receita_id=b["receita_id"])
        self.assertTrue(cmv_b2["success"], cmv_b2)
        self.assertEqual(cmv_b2["compras_total_estimado"], 16.0)
        self.assertAlmostEqual(cmv_b2["cmv_total"], 0.5 * 14 + 1.0 * 16)
        reg_b2 = call("cardapio_registrar", prato_id=cmv_b2["prato_id"], preco_escolhido=12)
        self.assertTrue(reg_b2["success"], reg_b2)
        self.assertEqual(reg_b2["orcamento"]["comprometido"], 16.0)
        frango = call("despensa_buscar", ingredientes=["peito de frango"])["resultados"][0]["candidatos"][0]
        self.assertEqual((frango["reservado_legivel"], frango["disponivel_legivel"]), ("2 kg", "0 g"))


if __name__ == "__main__":
    unittest.main()
