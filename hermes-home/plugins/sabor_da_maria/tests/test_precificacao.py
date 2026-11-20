import json
import unittest

from sabor_da_maria import api, costing, falas, montagem, painel, precificacao, tools
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
        self.assertIsNotNone(self.ler("Sim, não vejo impedimento.", pergunta))
        self.assertIsNotNone(self.ler("Sim", f"O prato #1 é {self.nome}. Aceita preparar nessa versão?"))
        self.assertIsNone(self.ler("Sim", "Você tem frigideira para fazer o prato #1?"))
        self.assertIsNone(self.ler("Sim", f"Você confirma o preço de R$ 4,00 do prato {self.nome}?"))
        self.assertIsNone(self.ler("Sim", f"Você aprova o preço de R$ 4,00 do prato #1 {self.nome}?"))
        self.assertIsNone(self.ler("Sim", f"{pergunta} Você aceita preparar o prato #2 também?"))
        self.assertIsNone(self.ler("Sim", f"{pergunta} Quer comprar sal?"))

    def test_recusa_negacao_aceite_condicional_e_referencias_ambiguas(self):
        pergunta = f"Você aceita preparar o prato #1 {self.nome} nessa versão?"
        for fala in ("Não aceito o prato #1.", "Sim, mas não aceito #1.", "Sim, não prepare.", "Sim, não faça nada.",
                     "Aceito #1 se tiver tomate.",
                     "Confirmo o prato #1, mas troque o feijão.", "Confirmo #2", "Confirmo #1 ou #2.",
                     "Confirmo que tenho frigideira para o prato #1.", "Confirmo o prato #1?",
                     "Confirmo só os ingredientes do prato #1.", "Confirmo o preço do prato #1."):
            with self.subTest(fala=fala):
                self.assertIsNone(self.ler(fala, pergunta))

    def test_nao_reutiliza_aceite_de_turno_passado(self):
        anterior = falas.Fala(f"Aceito {self.nome}.")
        self.assertIsNone(self.ler("Qual é o saldo?", anteriores=[anterior]))
        self.assertIsNone(self.ler("Não, prefiro revisar.", anteriores=[anterior]))

    def test_confirmacao_com_impedimento_ou_revisao_nao_aceita_ficha_antiga(self):
        pergunta = f"Você aceita preparar o prato #1 {self.nome} nessa versão?"
        for fala in ("Sim, aceito o prato #1, mas não tenho forno.",
                     "Sim, aceito o prato #1, só que acabou o gás.",
                     "Sim, aceito o prato #1 e troque o frango.",
                     "Sim, aceito o prato #1. Não tenho forno.",
                     "Sim, aceito o prato #1. Acabou o gás."):
            with self.subTest(fala=fala):
                self.assertIsNone(self.ler(fala, pergunta))
        self.assertIsNotNone(self.ler("Sim, não vejo impedimento.", pergunta))
        self.assertIsNotNone(self.ler("Pode preparar o prato #1 nessa versão.", pergunta))


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
        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, nome="Frango em cubos", porcoes=3,
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

    def test_aceite_separa_estoque_e_complementos_antes_da_escolha_do_preco(self):
        store = Store()
        prato, = store.selecionar_pratos([{"nome": "Frango em cubos"}])
        ficha = call("receita_conferir_ingredientes", prato_id=prato["id"], porcoes=3, tempo_preparo_min=30,
                     ingredientes=[{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"},
                                   {"nome": "Cogumelo shiitake", "quantidade": 50, "unidade": "g",
                                    "quantidade_compra": 100, "unidade_compra": "g", "preco_compra_estimado": 12}])
        self.assertEqual(ficha["veredito"], "viavel", ficha)
        ident = prato["id"]
        self.assertTrue(call("prato_calcular_cmv", prato_id=ident)["success"])
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.reservas_ativas(), [])

        diz(f"Aceito preparar o prato #{ident} nessa versão.")
        for _ in range(2):
            self.assertTrue(self.sugerir(ident)["analise_salva"])
            self.assertIsNone(store.obter_prato(ident)["preco_venda"])
            self.assertEqual(len(store.carrinho_itens()), 2)
            self.assertEqual(store.carrinho_resumo()["saldo"], 68)
            self.assertAlmostEqual(store.obter("Peito de frango")["disponivel_base"], 1.5)
        self.assertEqual(store.orcamento()["gasto_confirmado"], 0)

        diz(f"Escolho vender o prato #{ident} por R$ 12,00 por porção. Registre no cardápio.")
        publicado = call("cardapio_registrar", prato_id=ident, preco_escolhido=12)
        self.assertTrue(publicado["success"], publicado)
        self.assertEqual(publicado["linhas_do_carrinho_que_viraram_compras"], 2)
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.carrinho_resumo()["saldo"], 68)
        self.assertAlmostEqual(store.obter("Peito de frango")["disponivel_base"], 1.5)

    def test_preco_curto_registra_prato_que_estava_em_analise(self):
        store = Store()
        prato, = store.selecionar_pratos([{"nome": "Frango em cubos"}])
        prato_id = self.prato(selecionado=prato["id"])
        pergunta = ("O prato Frango em cubos ainda não tem preço definido. Escolha o preço para registrar no cardápio. "
                    "Qual preço você prefere?")
        historico = [{"role": "assistant", "content": pergunta}]
        diz(f"Confirmo o prato #{prato_id} nessa versão.")
        self.assertTrue(self.sugerir(prato_id)["analise_salva"])

        recomendacao = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="recomendacao", conversation_history=historico,
                                             user_message="faz pra mim isso, para eu ter lucro")["context"]
        self.assertIn("Sugiro", recomendacao)
        texto = montagem.completar_resposta("Vou simular novos preços", SESSAO, "recomendacao")
        self.assertIn("10%", texto)
        self.assertIsNone(store.obter_prato(prato_id)["preco_venda"])

        frase = "R$ 8,50"
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=frase)
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="registro", conversation_history=historico,
                                          user_message=frase)["context"]
        self.assertIn("Registro concluído no cardápio", resposta)
        self.assertIn("R$ 8,50 por porção", montagem.completar_resposta("Qual preço você prefere?", SESSAO, "registro"))
        self.assertEqual(store.obter_prato(prato_id)["preco_venda"], 8.50)

        reservas = store.reservas_ativas()
        self.assertEqual(len(reservas), 1)
        self.assertTrue(call("cardapio_registrar", prato_id=prato_id, preco_escolhido=8.50)["ja_registrado"])
        self.assertEqual(store.reservas_ativas(), reservas)

        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="8 reais")
        self.assertFalse(call("cardapio_registrar", prato_id=prato_id, preco_escolhido=8)["success"])
        self.assertEqual(store.obter_prato(prato_id)["preco_venda"], 8.50)

    def test_escolha_natural_com_ponto_registra_apos_pergunta_canonica_sem_repetir_id(self):
        store = Store()
        escolhido, = store.selecionar_pratos([{"nome": "Frango em cubos"}])
        prato_id = self.prato(selecionado=escolhido["id"])
        diz(f"Confirmo o prato #{prato_id} nessa versão.")
        analise = self.sugerir(prato_id)
        self.assertTrue(analise["analise_salva"])
        pergunta = tools._mostrar_analise(SESSAO, store.obter_prato(prato_id), analise)
        historico = [{"role": "assistant", "content": pergunta}]
        frase = "Escolho vender por R$ 6.50 por porção. Pode registrar."
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=frase)
        resposta = tools.avancar_cardapio_no_turno(store, SESSAO, falas.da_sessao(SESSAO))
        self.assertIn("Registro concluído no cardápio", resposta)
        self.assertEqual(store.obter_prato(prato_id)["preco_venda"], 6.5)
        self.assertEqual(store.obter_prato(prato_id)["status"], "aceito")
        reservas = store.reservas_ativas()
        self.assertEqual(len(reservas), 1)
        registro = call("cardapio_registrar", prato_id=prato_id, preco_escolhido=6.5)
        self.assertTrue(registro["success"], registro)
        self.assertTrue(registro["ja_registrado"])
        self.assertEqual(store.reservas_ativas(), reservas)
        self.assertEqual(store.orcamento()["saldo"], 80)
        outra_sessao = "preco-sem-pergunta-desta-conversa"
        falas.ao_iniciar_turno(session_id=outra_sessao, user_message="Escolho vender por R$ 9.50 por porção. Pode registrar.")
        self.assertIsNone(tools.avancar_cardapio_no_turno(store, outra_sessao, falas.da_sessao(outra_sessao)))
        recusado = json.loads(tools.HANDLERS["cardapio_registrar"]({"prato_id": prato_id, "preco_escolhido": 9.5}, session_id=outra_sessao))
        self.assertFalse(recusado["success"])
        self.assertEqual(store.obter_prato(prato_id)["preco_venda"], 6.5)

    def test_recalculo_no_mesmo_turno_atualiza_texto_para_ultima_analise_salva(self):
        prato = self.prato()
        diz(f"Confirmo o prato #{prato} nessa versão.")
        montagem.iniciar_turno(SESSAO, "recalculo-de-cenarios")
        anterior = self.sugerir(prato)
        self.assertTrue(anterior["analise_salva"], anterior)
        atual = self.sugerir(prato, margens=[0.55, 0.70], nomes=["Revisado", "Mais fôlego"])
        self.assertTrue(atual["analise_salva"], atual)
        self.assertNotEqual([c["preco_sugerido"] for c in atual["cenarios"]],
                            [c["preco_sugerido"] for c in anterior["cenarios"]])
        salva, = Store().analises_preco()
        self.assertEqual(salva["dados"]["cenarios"], atual["cenarios"])
        texto = montagem.completar_resposta("Preço inventado: R$ 99,00", SESSAO, "recalculo-de-cenarios")
        for cenario in atual["cenarios"]:
            self.assertIn(f"**{cenario['nome']}: {costing.brl(cenario['preco_sugerido'])}**", texto)
        for cenario in anterior["cenarios"]:
            self.assertNotIn(f"**{cenario['nome']}: {costing.brl(cenario['preco_sugerido'])}**", texto)
        self.assertNotIn("R$ 99,00", texto)
        self.assertIn("Qual preço por porção", texto)

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
        self.assertEqual((analise["cmv_total"], analise["cmv_por_porcao"], analise["preco_minimo"]), (7.0, 2.33, 2.60))
        self.assertEqual((analise["taxa_plataforma"], analise["cenario_recomendado"]), (0.10, 1))
        self.assertEqual(analise["cenarios"][0]["porcoes_para_meta"], 147)

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
        self.assertEqual(dados["perfil_precificacao"]["imposto"], 0)

        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(prato)["analise_salva"])
        anterior, = self.analises()
        status, dados = api.tratar("PUT", "/api/analytics/taxas", {"plano": "entrega", "imposto": 0.04})
        self.assertEqual(status, 200)
        taxa_nova, = dados["analises"]
        self.assertEqual(taxa_nova["preco_minimo"], anterior["preco_minimo"])
        self.assertEqual(Store().perfil_precificacao(), dados["perfil_precificacao"])
        self.assertEqual(api.tratar("PUT", "/api/analytics/taxas", {"plano": "entrega", "imposto": 0.80})[1]["perfil_precificacao"], dados["perfil_precificacao"])

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

    def test_perfil_real_salvo_no_banco_legado_e_normalizado_sem_mudar_decisao_da_pessoa(self):
        store = Store()
        legado = {"plano": "entrega", "comissao": 0.23, "pagamento": 0.032,
                  "pagamento_online": True, "imposto": 0.04}
        with store.connect() as conn:
            store._config_set(conn, "perfil_precificacao", json.dumps(legado))
        dados = api.tratar("GET", "/api/analytics/analises")[1]
        self.assertEqual(dados["perfil_precificacao"], {"plano": "desafio", "comissao": 0.10,
                         "pagamento": 0.0, "pagamento_online": False, "imposto": 0.0})
        self.assertEqual(list(dados["planos"]), ["desafio"])
        prato = self.prato()
        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(prato)["analise_salva"])
        antiga = store.analises_preco()[0]["dados"]
        antiga.update(taxa_plataforma=0.262, taxa_impostos=0.04, preco_minimo=99)
        store.salvar_analise_preco(prato, antiga)
        analise, = self.analises()
        self.assertEqual(analise["taxa_plataforma"], 0.10)
        self.assertEqual(analise["taxa_impostos"], 0.0)
        self.assertEqual(analise["preco_minimo"], 2.60)
        self.assertIsNone(store.obter_prato(prato)["preco_venda"])

    def test_revisao_do_mesmo_rascunho_recalcula_os_ingredientes(self):
        escolhido, = Store().selecionar_pratos([{"nome": "Frango em cubos"}])
        prato = self.prato(selecionado=escolhido["id"])
        diz("Confirmo o frango em cubos.")
        self.assertTrue(self.sugerir(prato)["analise_salva"])

        receita = call("receita_conferir_ingredientes", tempo_preparo_min=30, prato_id=prato, nome="Frango em cubos", porcoes=4,
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
