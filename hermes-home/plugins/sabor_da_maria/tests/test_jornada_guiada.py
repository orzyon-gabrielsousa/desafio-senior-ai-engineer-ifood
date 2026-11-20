"""A jornada espera escolhas reais e preserva estoque, dinheiro e fichas salvas."""

import json

from sabor_da_maria import api, encerramento, falas, jornada, montagem, negocio, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class JornadaGuiadaTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()

    def negocio(self, **extras):
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"],
                                  "estado": "SP", "cidade": "São Paulo", **extras})

    def fotografia(self):
        with self.store.connect() as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                    for t in ("ingredientes", "perfil", "receitas", "pratos", "analises_preco",
                              "reservas", "compras", "carrinho", "vendas")}

    def controle_salvo(self):
        with self.store.connect() as conn:
            return self.store._config_get(conn, jornada.CHAVE_CONTROLE)

    def turno(self, texto, anterior="", *, turno="guiado", historico=None):
        historico = historico if historico is not None else ([{"role": "assistant", "content": anterior}] if anterior else [])
        falas.ao_iniciar_turno(session_id="jornada-guiada", conversation_history=historico, user_message=texto)
        resposta = painel.ao_iniciar_turno(session_id="jornada-guiada", turn_id=turno,
                                          conversation_history=historico, user_message=texto)
        self.assertIsNotNone(resposta)
        return resposta["context"], montagem.resposta_preparada("jornada-guiada", turno)

    def ficha_viavel(self, *, preco=None):
        self.store.perfil_salvar({"equipamentos": {"forno": True, "forma": True},
                                 "tecnicas": {"assar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "botijão",
                                                "energia": "estável", "espaco_geladeira": "médio"}})
        escolhido, = self.store.selecionar_pratos([{"nome": "Batata assada da casa"}])
        dados = {"ingredientes": [{"nome": "Batata", "quantidade": 200, "unidade": "g"}],
                 "tempo_preparo_min": 40, "equipamentos_necessarios": ["forno", "forma"],
                 "tecnicas_necessarias": ["assar"]}
        rid = self.store.salvar_receita(escolhido["nome"], None, 2, dados, "conferida", prato_id=escolhido["id"])
        receita = self.store.obter_receita(rid)
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], receita)
        self.assertEqual(aval["veredito"], "viavel", aval)
        total = aval["resultado"]["cmv_total"]
        self.store.salvar_prato(escolhido["nome"], rid, 2, total, total / 2, dados, prato_id=escolhido["id"])
        if preco is not None:
            self.store.aceitar_prato(escolhido["id"], preco, [], tools._reservas_de(aval["resultado"]))
        return escolhido["id"]

    def test_negocio_vem_primeiro_e_nao_precisa_responder_tudo_de_uma_vez(self):
        atual = jornada.resumo(self.store)
        self.assertEqual(atual["passo"], "negocio")
        self.assertEqual(atual["pendencias"][0]["campo"], "tipo")
        self.store.negocio_atualizar({"tipo": "marmitaria"})
        atual = jornada.resumo(self.store)
        self.assertEqual(atual["passo"], "negocio")
        self.assertEqual(atual["pendencias"][0]["campo"], "alimentacao")
        self.assertEqual(self.store.pratos(), [])
        for acao in ("confirmar_estoque", "ajuda_ia", "prato_proprio", "interesse_divulgacao"):
            with self.subTest(acao=acao), self.assertRaises(ValueError):
                jornada.responder(self.store, acao)
        self.assertIsNone(self.controle_salvo())

    def test_consulta_e_estoque_ja_lido_nao_substituem_prontidao(self):
        self.negocio()
        self.store.registrar_consulta_despensa()
        api.estado(self.store)  # Inicializa apenas os dados derivados anteriores à fotografia.
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        for _ in range(2):
            atual = api.tratar("GET", "/api/estado")[1]["jornada"]
            self.assertEqual(atual["passo"], "estoque")
            self.assertFalse(atual["controle"]["estoque_confirmado"])
            self.assertFalse(atual["concluidas"]["estoque"])
            self.assertTrue(atual["estoque"])
        self.assertIsNone(self.controle_salvo())
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_pausa_sobrevive_reabertura_sem_confirmar_nem_pesquisar(self):
        self.negocio()
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        pausado = jornada.responder(self.store, "pausar")
        self.assertEqual(pausado["etapa"], "pausa")
        self.assertEqual(pausado["passo"], "estoque")
        nova = Store(self.store.db_path, self.store.xlsx_path)
        self.assertTrue(jornada.resumo(nova)["controle"]["pausado"])
        self.assertTrue(jornada.pediu_continuidade("Podemos continuar?"))
        jornada.contexto(nova)
        self.assertFalse(jornada.resumo(nova)["controle"]["estoque_confirmado"])
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_retomar_reabre_a_pergunta_sem_aceitar_por_ela(self):
        self.negocio()
        jornada.responder(self.store, "pausar")
        retomada = jornada.responder(self.store, "retomar")
        self.assertEqual(retomada["etapa"], "estoque")
        self.assertFalse(retomada["controle"]["pausado"])
        self.assertFalse(retomada["controle"]["estoque_confirmado"])
        self.assertEqual(self.store.pratos(), [])

    def test_sim_confirma_estoque_e_pede_ajuda_sem_criar_prato_ou_gasto(self):
        self.negocio()
        jornada.responder(self.store, "pausar")
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        confirmado = jornada.responder(self.store, "confirmar_estoque")
        self.assertEqual(confirmado["passo"], "ajuda")
        self.assertFalse(confirmado["controle"]["pausado"])
        self.assertTrue(confirmado["controle"]["estoque_confirmado"])
        self.assertEqual(confirmado["controle"]["ajuda"], "")
        self.assertEqual({o["acao"] for o in confirmado["pergunta"]["opcoes"]}, {"ajuda_ia", "prato_proprio"})
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_ajuda_exige_prontidao_e_escolha_nao_aceita_nem_debita(self):
        self.negocio()
        for acao in ("ajuda_ia", "prato_proprio"):
            with self.subTest(acao=acao), self.assertRaises(ValueError):
                jornada.responder(self.store, acao)
        jornada.responder(self.store, "confirmar_estoque")
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        escolha = jornada.responder(self.store, "ajuda_ia")
        self.assertEqual(escolha["passo"], "montagem")
        self.assertEqual(escolha["etapa"], "pesquisa")
        self.assertEqual(jornada.resumo(Store())["controle"]["ajuda"], "ia")
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_proprio_suspende_ficha_anterior_sem_apagar_ou_aprovar(self):
        self.negocio()
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        anterior = self.ficha_viavel()
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        escolha = jornada.responder(self.store, "prato_proprio")
        self.assertEqual(escolha["passo"], "montagem")
        self.assertEqual(escolha["etapa"], "proprio")
        self.assertTrue(escolha["controle"]["proprio_pendente"])
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)
        self.assertIsNone(self.store.obter_prato(anterior)["preco_venda"])
        novo, = self.store.selecionar_pratos([{"nome": "Meu novo prato"}])
        retomada = jornada.resumo(self.store)
        self.assertFalse(retomada["controle"]["proprio_pendente"])
        self.assertEqual(retomada["prato_id"], novo["id"])
        self.assertIsNotNone(self.store.obter_prato(anterior))

    def test_cinco_passos_vem_antes_da_divulgacao_e_preco_e_decisao_separada(self):
        self.assertEqual(jornada.resumo(self.store)["passo"], "negocio")
        self.negocio()
        self.assertEqual(jornada.resumo(self.store)["passo"], "estoque")
        self.assertEqual(jornada.responder(self.store, "confirmar_estoque")["passo"], "ajuda")
        self.assertEqual(jornada.responder(self.store, "ajuda_ia")["passo"], "montagem")
        pid = self.ficha_viavel()
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        with self.assertRaises(ValueError):
            jornada.responder(self.store, "interesse_divulgacao")
        # Esta análise representa a versão já aceita, ainda aguardando preço.
        self.store.salvar_analise_preco(pid, {"cotacao": "teste-jornada", "cenarios": []})
        precificar = jornada.resumo(self.store)
        self.assertEqual(precificar["passo"], "divulgacao")
        self.assertEqual(precificar["etapa"], "preco")
        self.assertTrue(precificar["concluidas"]["montagem"])
        self.assertFalse(precificar["concluidas"]["divulgacao"])
        self.assertIsNone(self.store.obter_prato(pid)["preco_venda"])
        with self.assertRaises(ValueError):
            jornada.responder(self.store, "dispensar_divulgacao")

    def test_divulgacao_so_depois_de_preco_e_viabilidade_sem_alterar_80(self):
        self.negocio()
        pid = self.ficha_viavel(preco=8.5)
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        atual = jornada.resumo(self.store)
        self.assertEqual(atual["passo"], "divulgacao")
        self.assertEqual(atual["etapa"], "divulgacao")
        self.assertFalse(atual["concluidas"]["divulgacao"])
        for acao, esperado in (("interesse_divulgacao", "interesse"), ("dispensar_divulgacao", "dispensada")):
            atual = jornada.responder(self.store, acao)
            self.assertEqual(atual["controle"]["divulgacao"], esperado)
            self.assertEqual(atual["concluidas"]["divulgacao"], esperado == "dispensada")
            self.assertEqual(self.fotografia(), antes)
            self.assertEqual(self.store.carrinho_resumo(), saldo)
        self.assertEqual(saldo["saldo"], 80)
        self.assertEqual(self.store.obter_prato(pid)["preco_venda"], 8.5)
        self.store.perfil_salvar({"equipamentos": {"forno": False}})
        with self.assertRaises(ValueError):
            jornada.responder(self.store, "interesse_divulgacao")

    def test_divulgacao_aceita_salva_plano_sem_perguntar_novamente(self):
        self.negocio()
        prato_id = self.ficha_viavel(preco=8.50)
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        sessao = "jornada-guiada"
        self.assertIsNone(jornada.acao_da_fala(jornada.resumo(self.store), "Sim, aprovo.", "Quer conferir a despensa?"))
        self.assertIsNone(jornada.acao_da_fala(jornada.resumo(self.store), "Sim, aprovo.",
                                               jornada.PERGUNTA_DIVULGACAO + " Quer conferir a despensa primeiro?"))
        criado_em = None
        acao_e_anterior = [("Sim, aprovo.", jornada.PERGUNTA_DIVULGACAO),
                           ("Sim, pode seguir.", "Posso organizar a divulgação da sua pizza?"),
                           (jornada.PEDIDO_DIVULGACAO, None),
                           ("Analise a divulgação do meu cardápio.", None),
                           ("Sim, pode simular.", "Quer simular com um valor de investimento e porções previstas?")]
        for indice, (mensagem, anterior) in enumerate(acao_e_anterior):
            turno = f"divulgacao-{indice}"
            contexto, preparada = self.turno(mensagem, anterior or "", turno=turno)
            self.assertIn("Chame divulgacao_planejar agora", contexto)
            self.assertIsNone(preparada)
            self.assertEqual(jornada.controle(self.store)["divulgacao"], "interesse")
            self.assertEqual(pesquisa.ferramenta_da_conducao(sessao), "divulgacao_planejar")
            requisicao = encerramento.executar(
                {"tools": [{"type": "function", "function": {"name": "divulgacao_planejar"}}]},
                lambda req: req, session_id=sessao, turn_id=turno, api_mode="chat_completions")
            self.assertEqual(requisicao["tool_choice"]["function"]["name"], "divulgacao_planejar")
            # Os argumentos do modelo não substituem os parâmetros do primeiro cenário padrão.
            plano = json.loads(tools.divulgacao_planejar({"porcoes": 99, "investimento": 100}, session_id=sessao))
            self.assertTrue(plano["success"], plano)
            self.assertEqual(plano["prato"]["id"], prato_id)
            self.assertEqual(plano["volume"]["porcoes"], 2)
            self.assertEqual(plano["campanha"]["investimento"], 0)
            self.assertEqual(plano["plano_novo"], indice == 0)
            self.assertIsNone(pesquisa.ferramenta_da_conducao(sessao))
            resposta = montagem.resposta_preparada(sessao, turno)
            self.assertIn("Mensagem pronta para usar" if indice == 0 else "já está salvo", resposta)
            self.assertNotIn("Quer simular", resposta)
            estado = jornada.resumo(self.store)
            self.assertTrue(estado["concluidas"]["divulgacao"])
            criado_em = criado_em or estado["plano_divulgacao"]["criado_em"]
            self.assertEqual(estado["plano_divulgacao"]["criado_em"], criado_em)
        consultado = json.loads(tools.divulgacao_planejar({}, session_id=sessao))
        self.assertEqual(consultado["criado_em"], criado_em)
        self.assertFalse(consultado["plano_novo"])
        simulado = json.loads(tools.divulgacao_planejar({"porcoes": 4, "investimento": 1}, session_id=sessao))
        self.assertEqual(simulado["volume"]["porcoes"], 4)
        self.assertNotIn("criado_em", simulado)
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_ficha_legada_retoma_ponto_atual_sem_migrar_ou_apagar_dados(self):
        self.negocio()
        pid = self.ficha_viavel()
        antes = self.fotografia()
        atual = api.estado(self.store)["jornada"]
        self.assertEqual(atual["passo"], "montagem")
        self.assertEqual(atual["prato_id"], pid)
        self.assertTrue(atual["controle"]["estoque_confirmado"])
        self.assertEqual(atual["controle"]["ajuda"], "ia")
        self.assertIsNone(self.controle_salvo())
        self.assertEqual(self.fotografia(), antes)

    def test_rascunho_com_falta_pode_aguardar_sem_bloquear_cardapio_existente(self):
        self.negocio()
        aprovado = self.ficha_viavel(preco=8.50)
        prato, = self.store.selecionar_pratos([{"nome": "Pão piloto"}])
        self.store.salvar_receita(prato["nome"], None, 2, {"ingredientes": [
            {"nome": "Fermento biológico", "quantidade": 5, "unidade": "g"},
        ], "tempo_preparo_min": 40}, "conferida", prato_id=prato["id"])
        antes = self.fotografia()
        self.assertEqual(jornada.resumo(self.store)["prato_id"], prato["id"])

        _, resposta = self.turno(f"Deixe o prato #{prato['id']} como rascunho para depois.", turno="depois")
        self.assertIn("Guardei **Pão piloto** como rascunho", resposta)
        self.assertEqual(jornada.resumo(self.store)["etapa"], "divulgacao")
        self.assertEqual(jornada.resumo(self.store)["pratos_ativos"], [aprovado])
        self.assertEqual(self.fotografia(), antes)

        self.turno(f"Continue o prato #{prato['id']}.", turno="retomar-ficha")
        self.assertEqual(jornada.resumo(self.store)["prato_id"], prato["id"])
        self.assertEqual(self.store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_objetivo_opcional_preservado_sem_bloquear_negocio_antigo(self):
        self.negocio()
        self.assertEqual(negocio.pendencias(self.store.negocio()), [])
        self.assertEqual(jornada.resumo(self.store)["passo"], "estoque")
        self.negocio(objetivo="Testar marmitas no almoço, sem ampliar os custos fixos.")
        self.assertEqual(Store().negocio()["objetivo"], "Testar marmitas no almoço, sem ampliar os custos fixos.")
        self.assertEqual(negocio.pendencias(self.store.negocio()), [])

    def test_api_aceita_so_acoes_conhecidas_e_nao_muda_saldo(self):
        self.negocio()
        antes, saldo = self.fotografia(), self.store.carrinho_resumo()
        for acao in ("pular_para_cardapio", None, False, [], {}):
            with self.subTest(acao=acao):
                self.assertEqual(api.tratar("POST", "/api/jornada", {"acao": acao})[0], 400)
        status, dados = api.tratar("POST", "/api/jornada", {"acao": "confirmar_estoque"})
        self.assertEqual(status, 200, dados)
        self.assertEqual(dados["jornada"]["passo"], "ajuda")
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), saldo)

    def test_sim_depende_da_pergunta_de_prontidao_mais_recente(self):
        self.negocio()
        antes = self.fotografia()
        pergunta = jornada.resposta_curta(self.store)
        for turno, texto, historico in (
                ("sem-contexto", "Sim", []),
                ("outra-pergunta", "Sim", [{"role": "assistant", "content": pergunta},
                                            {"role": "user", "content": "Qual é o saldo?"},
                                            {"role": "assistant", "content": "O saldo é R$ 80. Quer conferir o forno?"}]),
                ("citacao", '"Sim"', [{"role": "assistant", "content": pergunta}])):
            with self.subTest(turno=turno):
                self.turno(texto, turno=turno, historico=historico)
                self.assertFalse(jornada.resumo(self.store)["controle"]["estoque_confirmado"])
                self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(self.fotografia(), antes)
        contexto, pronta = self.turno("Sim", pergunta, turno="sim-prontidao")
        self.assertEqual(jornada.resumo(self.store)["passo"], "ajuda")
        self.assertIn("prefere montar", pronta)
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(self.fotografia(), antes)

    def test_nao_ao_estoque_pausa_e_continuar_nao_e_aceite(self):
        self.negocio()
        pergunta = jornada.resposta_curta(self.store)
        antes = self.fotografia()
        _, resposta = self.turno("Não", pergunta, turno="pausa-falada")
        self.assertIn("Vou aguardar", resposta)
        self.assertTrue(jornada.resumo(Store())["controle"]["pausado"])
        self.turno("Podemos continuar?", resposta, turno="retomar-falada")
        atual = jornada.resumo(Store())
        self.assertFalse(atual["controle"]["estoque_confirmado"])
        self.assertEqual(atual["passo"], "estoque")
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(self.fotografia(), antes)

    def test_cadastro_enviado_mostra_estoque_e_aguarda_sem_pesquisar(self):
        self.negocio()
        antes = self.fotografia()
        contexto, pronta = self.turno(
            "Enviei meu cadastro do negócio. Continue me conduzindo.\n[painel da tela: cadastro guiado no chat; salvou negocio]",
            turno="cadastro-negocio")
        self.assertIn("ingredientes já comprados", pronta)
        self.assertEqual(pronta.count("?"), 1)
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)
        self.assertNotIn("PESQUISA DE RECEITAS", contexto)
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(jornada.resumo(self.store)["passo"], "estoque")
        self.assertEqual(self.fotografia(), antes)

    def test_escolha_ia_arma_pesquisa_so_depois_da_prontidao(self):
        self.negocio()
        mensagem = "Quero sua ajuda. Pesquise uma receita real e monte uma proposta com meu estoque."
        contexto, _ = self.turno(mensagem, turno="ia-cedo")
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)
        self.turno("Sim, estou pronta para começar a montar meus pratos.", turno="pronta")
        self.assertEqual(jornada.resumo(self.store)["passo"], "ajuda")
        antes = self.fotografia()
        contexto, pronta = self.turno(mensagem, turno="ia-permitida")
        self.assertIsNone(pronta)
        self.assertIn("PESQUISA DE RECEITAS", contexto)
        self.assertTrue(pesquisa.somente_sugestoes("jornada-guiada"))
        self.assertEqual(pesquisa.ferramenta_da_conducao("jornada-guiada"), "despensa_listar")
        self.assertEqual(jornada.resumo(self.store)["controle"]["ajuda"], "ia")
        self.assertEqual(self.fotografia(), antes)

    def test_ajuda_condicional_ou_negada_nao_e_autorizacao_de_pesquisa(self):
        self.negocio()
        jornada.responder(self.store, "confirmar_estoque")
        for i, mensagem in enumerate(("Quero sua ajuda, mas não pesquise ainda.",
                                      "Quero ajuda da IA? Ainda não decidi.",
                                      "Se eu quiser sua ajuda, você pesquisa?")):
            with self.subTest(mensagem=mensagem):
                self.turno(mensagem, turno=f"nao-delegou-{i}")
                self.assertEqual(jornada.resumo(self.store)["passo"], "ajuda")
                self.assertEqual(jornada.resumo(self.store)["controle"]["ajuda"], "")
                self.assertFalse(pesquisa.pesquisando("jornada-guiada"))

    def test_escolha_propria_no_hook_nao_retoma_a_ficha_anterior(self):
        self.negocio()
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        pid = self.ficha_viavel()
        antes = self.fotografia()
        contexto, pronta = self.turno("Quero montar meu próprio prato.", turno="proprio-falado")
        self.assertEqual(jornada.resumo(self.store)["etapa"], "proprio")
        self.assertIn("Qual prato você tem em mente", pronta)
        self.assertNotIn(f"FICHA EM FOCO: prato #{pid}", contexto)
        self.assertFalse(pesquisa.pesquisando("jornada-guiada"))
        self.assertEqual(self.fotografia(), antes)
