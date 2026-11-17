import json
import unittest

from sabor_da_maria import api, falas, precificacao, tools
from sabor_da_maria.store import Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args, session_id=SESSAO))


class AceiteDaReceitaTests(unittest.TestCase):
    nome = "Filé de Peito Grelhado com Arroz Branco e Feijão Carioca"

    def ler(self, fala, pergunta="", anteriores=()):
        return precificacao.aceite_da_receita(1, self.nome, [*anteriores, falas.Fala(fala, resposta_a=pergunta)])

    def test_aceita_nome_ou_numero_sem_exigir_a_citacao_do_modelo(self):
        for fala in ("Confirmo o prato #1 nessa versão.", "Vamos preparar o prato 1.",
                     f"Sim, aceito preparar {self.nome} nessa versão."):
            with self.subTest(fala=fala):
                self.assertEqual(self.ler(fala).texto, fala)
        self.assertTrue(precificacao.confirmou_prato(None, self.nome, [falas.Fala("Confirmo #1")], prato_id=1))

    def test_sim_fica_ligado_a_uma_pergunta_de_aceite_de_receita(self):
        pergunta = f"O custo é R$ 3,49 por porção. Você aceita preparar o prato #1 {self.nome} nessa versão?"
        self.assertIsNotNone(self.ler("Sim, está certo, quero seguir com a receita do jeito que combinamos.", pergunta))
        self.assertIsNotNone(self.ler("Sim", f"O prato #1 é {self.nome}. Aceita preparar nessa versão?"))
        self.assertIsNone(self.ler("Sim", "Você tem frigideira para fazer o prato #1?"))
        self.assertIsNone(self.ler("Sim", f"Você confirma o preço de R$ 4,00 do prato {self.nome}?"))
        self.assertIsNone(self.ler("Sim", f"Você aprova o preço de R$ 4,00 do prato #1 {self.nome}?"))
        self.assertIsNone(self.ler("Sim", f"{pergunta} Você aceita preparar o prato #2 também?"))
        self.assertIsNone(self.ler("Sim", f"{pergunta} Quer comprar sal?"))

    def test_recusa_negacao_aceite_condicional_e_referencias_ambiguas(self):
        pergunta = f"Você aceita preparar o prato #1 {self.nome} nessa versão?"
        for fala in ("Não aceito o prato #1.", "Sim, mas não aceito #1.", "Aceito #1 se tiver tomate.",
                     "Confirmo o prato #1, mas troque o feijão.", "Confirmo #2", "Confirmo #1 ou #2.",
                     "Confirmo que tenho frigideira para o prato #1.", "Confirmo o prato #1?",
                     "Confirmo só os ingredientes do prato #1.", "Confirmo o preço do prato #1."):
            with self.subTest(fala=fala):
                self.assertIsNone(self.ler(fala, pergunta))

    def test_nao_reutiliza_aceite_de_turno_passado(self):
        anterior = falas.Fala(f"Aceito {self.nome}.")
        self.assertIsNone(self.ler("Qual é o saldo?", anteriores=[anterior]))
        self.assertIsNone(self.ler("Não, prefiro revisar.", anteriores=[anterior]))


class AnalisesDePrecoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        diz(FALA_COZINHA)
        perfil = call(
            "perfil_cozinha_salvar",
            equipamentos={"fogão": 4, "forno": True, "panela de pressão": True, "air fryer": False, "liquidificador": True},
            restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"},
            tecnicas={"refogado": "sim"},
        )
        self.assertTrue(perfil["completo"], perfil)

    def prato(self, quantidade=500, *, selecionado=None):
        receita = call("receita_conferir_ingredientes", nome="Frango em cubos", porcoes=3,
                       ingredientes=[{"nome": "peito de frango", "quantidade": quantidade, "unidade": "g"}],
                       prato_id=selecionado)
        cmv = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        return cmv["prato_id"]

    def sugerir(self, prato_id, **extras):
        opcoes = {"aceite_prato": "Confirmo o frango em cubos", "margens": [0.2, 0.35],
                  "nomes": ["Lançamento", "Fôlego"], "cenario_recomendado": 1,
                  "recomendacao": "O preço de lançamento permite testar um lote pequeno."}
        return call("preco_sugerir", prato_id=prato_id, **{**opcoes, **extras})

    def analises(self):
        status, dados = api.tratar("GET", "/api/analytics/analises")
        self.assertEqual(status, 200)
        self.assertEqual(dados["analises"], call("analytics_analises_consultar")["analises"])
        return dados["analises"]

    def test_aceite_da_receita_salva_analise_e_registro_reflete_decisao(self):
        prato = self.prato()
        avulsa = call("preco_sugerir", prato_id=prato)
        self.assertTrue(avulsa["success"], avulsa)
        self.assertFalse(avulsa["analise_salva"])
        self.assertEqual(self.analises(), [])

        recusada = self.sugerir(prato)
        self.assertEqual(recusada["bloqueios"][0]["tipo"], "sem_aceite_do_prato")
        self.assertEqual(self.analises(), [])

        diz("Confirmo o frango em cubos, pode sugerir os preços. Quero sobrar R$ 100 no mês e tenho R$ 20 de custos fixos.")
        sugerida = self.sugerir(prato, lucro_alvo=100, custos_fixos=20, taxa_plataforma=0.05)
        self.assertTrue(sugerida["analise_salva"], sugerida)
        analise, = self.analises()
        self.assertEqual((analise["status"], analise["preco_escolhido"]), ("proposta", None))
        self.assertEqual((analise["cmv_total"], analise["cmv_por_porcao"], analise["preco_minimo"]), (7.0, 2.33, 2.75))
        self.assertEqual((analise["taxa_plataforma"], analise["cenario_recomendado"]), (0.152, 1))
        self.assertEqual(analise["cenarios"][0]["porcoes_para_meta"], 188)

        registro = call("cardapio_registrar", prato_id=prato, preco_escolhido=4)
        self.assertTrue(registro["success"], registro)
        registrada, = self.analises()
        self.assertEqual((registrada["status"], registrada["preco_escolhido"]), ("registrado", 4))
        self.assertEqual(registrada["resultado_escolhido"], registro["resultado_por_porcao"])
        self.assertEqual(registrada["cenarios"], analise["cenarios"])

        call("cardapio_remover", prato_id=prato)
        self.assertEqual(self.analises(), [])

    def test_recalcula_ficha_taxas_e_preco_registrado(self):
        store = Store()
        escolhido, = store.selecionar_pratos([{"nome": "Frango em cubos"}])
        prato = self.prato(selecionado=escolhido["id"])
        dados = api.tratar("GET", "/api/analytics/analises")[1]
        previa, = dados["previas"]
        self.assertEqual((previa["status"], previa["cmv_total"]), ("previa", 7))
        self.assertIsNone(dados["perfil_precificacao"]["imposto"])

        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(prato)["analise_salva"])
        anterior, = self.analises()
        status, dados = api.tratar("PUT", "/api/analytics/taxas", {"plano": "entrega", "imposto": 0.04})
        self.assertEqual(status, 200)
        taxa_nova, = dados["analises"]
        self.assertGreater(taxa_nova["preco_minimo"], anterior["preco_minimo"])
        self.assertEqual(Store().perfil_precificacao(), dados["perfil_precificacao"])
        self.assertEqual(api.tratar("PUT", "/api/analytics/taxas", {"plano": "entrega", "imposto": 0.80})[0], 400)

        store.registrar_compra("Peito de frango", 1, "kg", 30)
        atual, = self.analises()
        self.assertGreater(atual["cmv_total"], anterior["cmv_total"])
        self.assertGreater(atual["preco_minimo"], taxa_nova["preco_minimo"])
        diz(f"Escolho vender o prato #{prato} por R$ 10,00 por porção. Registre no cardápio. Cotação {anterior['cotacao']}.")
        self.assertEqual(call("cardapio_registrar", prato_id=prato, preco_escolhido=10)["bloqueios"][0]["tipo"], "numeros_mudaram")
        diz(f"Escolho vender o prato #{prato} por R$ 10,00 por porção. Registre no cardápio. Cotação {atual['cotacao']}.")
        registro = tools.avancar_cardapio_no_turno(store, SESSAO, falas.da_sessao(SESSAO))
        self.assertIn("Registro concluído no cardápio", registro)

        antes, = self.analises()
        reservas = store.reservas_do_prato(prato)
        self.assertTrue(reservas)
        store.registrar_compra("Peito de frango", 1, "kg", 35)
        depois, = self.analises()
        self.assertGreater(depois["cmv_total"], antes["cmv_total"])
        self.assertLess(depois["resultado_escolhido"]["lucro"], antes["resultado_escolhido"]["lucro"])
        self.assertEqual((depois["status"], depois["preco_escolhido"]), ("registrado", 10))
        self.assertEqual(store.reservas_do_prato(prato), reservas)

    def test_revisao_do_mesmo_rascunho_recalcula_os_ingredientes(self):
        escolhido, = Store().selecionar_pratos([{"nome": "Frango em cubos"}])
        prato = self.prato(selecionado=escolhido["id"])
        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(prato)["analise_salva"])

        receita = call("receita_conferir_ingredientes", prato_id=prato, nome="Frango em cubos", porcoes=4,
                       ingredientes=[{"nome": "peito de frango", "quantidade": 750, "unidade": "g"},
                                     {"nome": "sal", "quantidade": 5, "unidade": "g"}])
        self.assertTrue(receita["success"], receita)
        dados = api.tratar("GET", "/api/analytics/analises")[1]
        self.assertEqual(dados["analises"], [])
        previa, = dados["previas"]
        self.assertEqual((previa["prato_id"], previa["porcoes"], previa["cmv_total"]), (prato, 4, 10.51))

    def test_revisao_substitui_analise_anterior_e_retira_sem_reexibir_a_antiga(self):
        primeira = self.prato()
        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(primeira)["success"])
        segunda = self.prato(600)
        self.assertTrue(self.sugerir(segunda)["success"])
        atual, = self.analises()
        self.assertEqual(atual["prato_id"], segunda)
        call("cardapio_remover", prato_id=segunda)
        self.assertEqual(self.analises(), [])

    def test_recusa_trecho_negado_e_sim_sem_contexto(self):
        prato = self.prato()
        diz("Eu não aceito o frango em cubos.", "Sim.")
        self.assertFalse(self.sugerir(prato, aceite_prato="aceito o frango em cubos")["success"])
        self.assertFalse(self.sugerir(prato, aceite_prato="Sim")["success"])

        falas.registrar(SESSAO, [
            {"role": "assistant", "content": "Você aceita preparar Frango em cubos nessa versão?"},
            {"role": "user", "content": "Sim."},
        ])
        self.assertTrue(self.sugerir(prato, aceite_prato="Sim")["analise_salva"])
