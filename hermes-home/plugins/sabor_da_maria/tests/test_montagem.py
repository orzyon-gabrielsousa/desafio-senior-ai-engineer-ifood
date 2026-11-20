import json

from sabor_da_maria import falas, montagem, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class MontagemTests(StoreTestCase):
    def _cozinha(self, *, forno=True):
        store = Store()
        store.cozinha_salvar({
            "equipamentos": {"fogao": 6, "forno": forno, "forma": forno, "panela_grande": True,
                             "panela_pressao": True, "air_fryer": True, "liquidificador": True},
            "habilidades": {"arroz_feijao": "sim", "assar": "sim", "refogar": "aprendendo" if forno else "sim"},
            "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"},
        })
        return store

    def _adaptar(self, store):
        mensagens = [{"role": "assistant", "content": "O prato Arroz de forno super-rápido ainda não foi registrado. Qual é o nome?"}]
        conversa = falas.extrair(mensagens, "monte um prato equivalente com esses mesmos ingredientes")
        return montagem.propor_turno(store, conversa, SESSAO)

    def test_adapta_a_receita_anterior_e_guarda_a_ficha_sem_compras_ou_reservas(self):
        store = self._cozinha()
        original = store.selecionar_pratos([{"nome": "Arroz de forno super-rápido"}, {"nome": "Macarrão bolonhesa"}])
        contexto = self._adaptar(store)
        self.assertIn("Arroz de forno com frango, tomate e mussarela", contexto)
        self.assertIn("sem requeijão", contexto)
        self.assertIn("Compras complementares: R$ 0,00", contexto)
        self.assertIn("aceita preparar o prato", contexto)
        pratos = store.pratos_em_andamento()
        self.assertEqual([p["id"] for p in pratos[:2]], [p["id"] for p in original])
        adaptacao = pratos[2]
        self.assertEqual((adaptacao["status"], adaptacao["veredito"], adaptacao["porcoes"]), ("selecionado", "viavel", 4))
        self.assertEqual(adaptacao["compras_estimadas"], 0)
        receita = store.obter_receita(adaptacao["receita_id"])
        self.assertTrue(all(store.obter(i["nome"]) for i in receita["dados"]["ingredientes"]))
        self.assertEqual(self._adaptar(store), contexto)
        self.assertEqual(len(store.receitas()), 1)
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_escolhe_variante_de_panela_quando_a_cozinha_nao_tem_forno(self):
        store = self._cozinha(forno=False)
        contexto = self._adaptar(store)
        self.assertIn("Arroz de panela com frango e tomate", contexto)
        self.assertIn("aceita preparar o prato", contexto)
        prato, = store.pratos_em_andamento()
        self.assertEqual(prato["veredito"], "viavel")

    def test_montagem_inicial_segue_para_pesquisa_com_ou_sem_globo(self):
        store = self._cozinha()
        for texto in ("monte um prato para meu cardápio", "Pesquise na internet receitas para este pedido:\n\nmonte um prato para meu cardápio"):
            conversa = falas.extrair([], texto)
            self.assertTrue(montagem.pediu_montagem(conversa[-1].texto))
            self.assertIsNone(montagem.propor_turno(store, conversa, SESSAO))
            self.assertEqual(store.pratos_em_andamento(), [])
        proposta = montagem.propor_turno(store, conversa, SESSAO, permitir_adaptacao_local=True)
        self.assertIn("Montei uma opção", proposta)
        self.assertIn("Adaptação local da despensa", proposta)
        self.assertIn("não foram lidos de uma receita na internet", proposta)

    def test_adaptacao_natural_preserva_a_fonte_e_espera_novo_aceite(self):
        store = self._cozinha()
        self.addCleanup(pesquisa.esquecer_tudo)
        original = {"nome": "Frango assado com batatas", "url": "https://exemplo.com/frango",
                    "porcoes": 5, "tempo_preparo_min": 55,
                    "ingredientes": ["1,5 kg de frango", "2 batatas grandes", "100 g de molho de tomate", "50 g de maionese",
                                     "10 g de tempero para aves", "2 dentes de alho", "Sal a gosto"],
                    "modo_preparo": "Misture o frango, a batata e os molhos numa assadeira e leve ao forno."}
        pesquisa.marcar_turno(SESSAO, "monte um prato para meu cardápio")
        pagina = {"url": original["url"], "title": original["nome"], "receitas": [original],
                  "content": "# Frango assado com batatas\n## Ingredientes\n- 1,5 kg de frango\n## Modo de preparo\nLeve ao forno."}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [pagina]}), session_id=SESSAO)
        bloqueada = json.loads(tools.receita_conferir_ingredientes({"porcoes": 4, "tempo_preparo_min": 55,
                         "ingredientes": [{"nome": "frango", "quantidade": 1, "unidade": "kg"},
                                          {"nome": "batata", "quantidade": 2, "unidade": "kg"}]}, session_id=SESSAO))
        self.assertEqual(bloqueada["bloqueios"][0]["tipo"], "fonte_incompleta")
        pedido = "Pode propor uma adaptação dessa receita com o que tenho no estoque?"
        conversa = falas.extrair([{"role": "assistant", "content": bloqueada["diga_a_dona_maria"]}], pedido)
        falas.registrar(SESSAO, [{"role": "assistant", "content": bloqueada["diga_a_dona_maria"]}], pedido)
        montagem.iniciar_turno(SESSAO, "adaptar-fonte")
        self.assertTrue(montagem.pediu_montagem(pedido))
        self.assertTrue(montagem.pediu_montagem("Adapte essa receita usando o estoque."))
        self.assertFalse(montagem.pediu_montagem("Adapte o preço para cobrir os custos."))
        contexto = montagem.propor_turno(store, conversa, SESSAO)
        self.assertIn("Peito de frango assado com batata", contexto)
        self.assertIn("Diferenças da fonte", contexto)
        self.assertIn("maionese", contexto)
        local = next(p for p in store.pratos_em_andamento() if p["id"] != bloqueada["prato_id"])
        self.assertEqual((local["status"], local["veredito"]), ("selecionado", "viavel"))
        receita = store.obter_receita(local["receita_id"])
        dados = receita["dados"]
        self.assertEqual(dados["fonte_verificada"], original)
        self.assertEqual(receita["fonte_url"], original["url"])
        self.assertTrue(dados["fidelidade_fonte"]["diferencas"])
        self.assertEqual(dados["modo_preparo"], dados["preparo_da_adaptacao"])
        self.assertNotEqual(dados["modo_preparo"], original["modo_preparo"])
        self.assertEqual([(i["nome"], i["quantidade"], i["unidade"]) for i in dados["ingredientes"][:2]],
                         [("Peito de frango", 480, "g"), ("Batata", 600, "g")])
        self.assertEqual(store.obter_prato(bloqueada["prato_id"])["status"], "selecionado")
        self.assertEqual(store.reservas_ativas(), [])
        cmv = json.loads(tools.prato_calcular_cmv({"prato_id": local["id"]}, session_id=SESSAO))
        self.assertTrue(cmv["success"], cmv)
        antes = json.loads(tools.preco_sugerir({"prato_id": local["id"]}, session_id=SESSAO))
        self.assertFalse(antes["analise_salva"], antes)
        apresentada = montagem.completar_resposta("Vou propor uma adaptação", SESSAO, "adaptar-fonte")
        self.assertIn("Diferenças da fonte", apresentada)
        falas.registrar(SESSAO, [{"role": "assistant", "content": apresentada}], "Sim, aceito essa versão.")
        aceita = json.loads(tools.preco_sugerir({"prato_id": local["id"]}, session_id=SESSAO))
        self.assertTrue(aceita["analise_salva"], aceita)
        self.assertEqual({r["prato_id"] for r in store.reservas_ativas()}, {local["id"]})
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_so_substitui_por_uma_variante_relacionada(self):
        store = self._cozinha()
        self.assertIsNone(montagem.propor_alternativa(store, "Sushi combinado", SESSAO))
        self.assertEqual(store.pratos_em_andamento(), [])
        proposta = montagem.propor_alternativa(store, "Arroz de forno super-rápido", SESSAO)
        self.assertIn("Arroz de forno com frango, tomate e mussarela", proposta)
        self.assertEqual(store.reservas_ativas(), [])

    def test_bloqueio_posterior_nao_apaga_proposta_viavel_e_suas_diferencas(self):
        montagem.iniciar_turno(SESSAO, "proposta-seguida-de-bloqueio")
        proposta = ["Prato #2 viável: peito de frango assado com batata.",
                    "Diferenças da fonte: retirei maionese; usei 480 g de peito e 600 g de batata.",
                    "Você aceita preparar o prato #2 nessa versão?"]
        montagem.registrar_resposta(SESSAO, proposta, substituir=True, prioridade=20)
        montagem.registrar_resposta(SESSAO, ["O prato #3 está bloqueado."], substituir=True, prioridade=10)
        resposta = montagem.completar_resposta("O prato #3 está bloqueado.", SESSAO, "proposta-seguida-de-bloqueio")
        self.assertIn("Prato #2 viável", resposta)
        self.assertIn("Diferenças da fonte", resposta)
        self.assertNotIn("#3", resposta)

    def test_analise_e_registro_prevalecem_sobre_conferencias_posteriores(self):
        casos = (
            ([(20, "Proposta viável"), (30, "Análise salva"), (20, "Outra proposta"), (10, "Bloqueada")], "Análise salva"),
            ([(30, "Análise salva"), (40, "Registro concluído"), (30, "Outra análise"), (10, "Bloqueada")], "Registro concluído"),
            ([(20, "Primeira proposta"), (20, "Proposta atualizada")], "Proposta atualizada"),
        )
        for indice, (eventos, esperado) in enumerate(casos):
            with self.subTest(esperado=esperado):
                turno = f"prioridades-{indice}"
                montagem.iniciar_turno(SESSAO, turno)
                for prioridade, texto in eventos:
                    montagem.registrar_resposta(SESSAO, [texto], substituir=True, prioridade=prioridade)
                self.assertEqual(montagem.completar_resposta("Resposta desatualizada", SESSAO, turno), esperado)

    def test_novo_turno_limpa_a_prioridade_do_registro_anterior(self):
        montagem.iniciar_turno(SESSAO, "registro-antigo")
        montagem.registrar_resposta(SESSAO, ["Registro concluído"], substituir=True, prioridade=40)
        montagem.iniciar_turno(SESSAO, "nova-pergunta")
        montagem.registrar_resposta(SESSAO, ["Confirme a quantidade"], substituir=True, prioridade=10)
        self.assertIsNone(montagem.completar_resposta("Texto antigo", SESSAO, "registro-antigo"))
        self.assertEqual(montagem.completar_resposta("Vou conferir", SESSAO, "nova-pergunta"), "Confirme a quantidade")

    def test_completa_resposta_curta_com_a_ficha_do_mesmo_turno(self):
        store = self._cozinha()
        montagem.iniciar_turno(SESSAO, "adaptacao")
        self._adaptar(store)
        curto = "Você aceita preparar o prato #1, Arroz de forno com frango, tomate e mussarela, nessa versão?"
        self.assertIsNone(montagem.completar_resposta(curto, SESSAO, "turno-anterior"))
        texto = montagem.completar_resposta(curto, SESSAO, "adaptacao")
        self.assertTrue(texto.startswith(curto))
        self.assertIn("Ingredientes do lote", texto)
        self.assertIn("de 4 porções", texto)
        self.assertIn("Compras complementares: R$ 0,00", texto)
        self.assertEqual(texto.count("Você aceita preparar"), 1)
        self.assertIsNone(montagem.completar_resposta(curto, SESSAO, "adaptacao"))
