from sabor_da_maria import api, escolhas, falas, jornada, painel
from sabor_da_maria.store import Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz
from .test_costing_and_tools import call


OPCOES = """Aqui estão duas sugestões.

### ✅ **1. Filé de Peito Grelhado com Arroz Branco e Feijão Carioca**
- **Fonte:** [Receita](https://example.com/frango)
- **Já tem na despensa:** arroz e feijão

### ✅ **2. Almoço Prático: Arroz, Feijão e Frango Grelhado**
- **Fonte:** [Receita](https://example.com/almoco)
- **Precisa comprar:** tempero para aves

### Próximos passos
1. Escolha o que prefere.
2. Confirme o rendimento.
"""

OPCOES_DE_FORNO_E_MASSA = """#### 1. Arroz de forno super-rápido

**Fonte:** [**Arroz de forno super-rápido**](https://example.com/receitas)

**Ingredientes confirmados:** arroz, frango e mussarela.

**Faltando:** requeijão.

Você gosta de cozinhar isso? Vê algum impedimento?

---

#### 2. Macarrão bolonhesa

**Fonte:** [**Macarrão bolonhesa**](https://example.com/receitas)

**Ingredientes confirmados:** espaguete, carne moída e tomate.

Você gosta de cozinhar isso? Vê algum impedimento?
"""


class EscolhasTests(StoreTestCase):
    def iniciar_escolha_apos_pesquisa(self):
        store = Store()
        store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"], "estado": "SP", "cidade": "São Paulo"})
        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "ajuda_ia")
        return store

    def test_conferencia_aceite_e_preco_avancam_o_mesmo_prato(self):
        store = Store()
        diz(FALA_COZINHA)
        perfil = call("perfil_cozinha_salvar", equipamentos={"fogão": 4, "forno": True, "panela de pressão": True,
                      "air fryer": False, "liquidificador": True}, tecnicas={"refogado": "sim"},
                      restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"})
        self.assertTrue(perfil["completo"])
        prato, outro = store.selecionar_pratos([{"nome": "Frango em cubos"}, {"nome": "Frango com arroz"}])
        prato_id = prato["id"]
        self.assertFalse(call("receita_conferir_ingredientes", tempo_preparo_min=30, porcoes=3, ingredientes=["peito de frango"])["success"])
        diz(f"Quero continuar o prato escolhido #{prato_id} e conferir a ficha.")
        ficha = call("receita_conferir_ingredientes", tempo_preparo_min=30, porcoes=3,
                     ingredientes=[{"nome": "peito de frango", "quantidade": 500, "unidade": "g"}])
        self.assertEqual((ficha["prato_id"], ficha["receita"], ficha["veredito"]), (prato_id, prato["nome"], "viavel"))
        self.assertIn("Saldo atual para compras: R$ 80,00", ficha["diga_a_dona_maria"])
        self.assertIsNone(store.obter_prato(outro["id"])["receita_id"])

        aceite = f"Confirmo #{prato_id} Frango em cubos nessa versão."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=aceite)
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, user_message=aceite)["context"]
        self.assertIn("Aceite já confirmado. A análise", contexto)
        self.assertEqual(store.obter_prato(prato_id)["status"], "cmv_calculado")
        propostas = api.tratar("GET", "/api/analytics/analises")[1]["analises"]
        self.assertEqual([a["prato_id"] for a in propostas], [prato_id])
        self.assertTrue(call("prato_calcular_cmv", prato_id=prato_id)["success"])
        self.assertEqual(len(store.analises_preco()), 1)
        self.assertEqual([r["quantidade"] for r in store.reservas_do_prato(prato_id)], ["500 g"])
        self.assertEqual(store.orcamento()["saldo"], 80)
        self.assertFalse(call("cardapio_registrar", prato_id=prato_id, preco_escolhido=6)["success"])

        pedido = f"Escolho vender o prato #{prato_id} por R$ 6,00 por porção. Registre no cardápio."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=pedido)
        for _ in range(2):
            contexto = painel.ao_iniciar_turno(session_id=SESSAO, user_message=pedido)["context"]
            self.assertIn("Registro concluído no cardápio", contexto)
        prato = store.obter_prato(prato_id)
        self.assertEqual((prato["status"], prato["preco_venda"], prato["receita_id"]), ("aceito", 6, ficha["receita_id"]))
        self.assertEqual(len(store.reservas_do_prato(prato_id)), 1)
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_comando_de_preco_exige_um_valor_e_pedido_de_registro(self):
        for texto in ("Confirmo o prato #1", "R$ 6,00 ou R$ 8,00?", "Escolho R$ 6 mas não registre no cardápio",
                      "Registre no cardápio por R$ 6 ou R$ 8", "Se eu confirmar R$ 6, registre no cardápio",
                      "Copiei do chat: Escolho R$ 5,00. Registre o prato #1 no cardápio.",
                      "Escolho R$ 5,00, mas quero revisar os ingredientes antes de registrar o prato #1 no cardápio.",
                      "Não tenho certeza de que deve registrar o prato #1 no cardápio por R$ 5,00."):
            self.assertIsNone(escolhas.preco_para_registrar(texto))
        self.assertEqual(escolhas.preco_para_registrar("Registre o prato #1 no cardápio por R$ 6,50"), 6.5)
        pergunta = "Prato #1, Frango em cubos, a R$ 5,00. Qual preço você escolhe?"
        self.assertFalse(escolhas.pediu_registro_do_prato("Escolho R$ 5,00; registre o outro prato no cardápio.", pergunta, 1, "Frango em cubos"))
        self.assertTrue(escolhas.pediu_registro_do_prato("Escolho R$ 5,00. Registre no cardápio.", pergunta, 1, "Frango em cubos"))

    def test_preco_curto_responde_ao_pedido_de_registro_da_conversa(self):
        nome = "Arroz de forno com frango, tomate e mussarela"
        oferta = (
            f"O prato **{nome}** está pronto para ser registrado no cardápio.\n\n"
            "Para começar, escolha um preço entre R$ 6,00 e R$ 10,00. Qual você prefere?\n"
            "Só depois, usarei cardapio_registrar para adicionar o prato ao cardápio oficial."
        )
        repete_sem_valores = (
            f"O prato **{nome}** ainda não tem preço definido. Por favor, escolha um dos cenários apresentados "
            "anteriormente para registrar o prato no cardápio. Qual preço você prefere?"
        )
        for resposta, anterior, preco in (("8 reais", oferta, 8), ("R$ 8,50", repete_sem_valores, 8.5)):
            with self.subTest(resposta=resposta):
                self.assertEqual(escolhas.preco_para_registrar(resposta, anterior), preco)
                self.assertTrue(escolhas.pediu_registro_do_prato(resposta, anterior, 1, nome))
                self.assertFalse(escolhas.pediu_registro_do_prato(resposta, anterior, 2, "Frango em cubos"))

    def test_preco_curto_respeita_ambiguidade_e_pergunta_atual(self):
        nome = "Arroz de forno com frango, tomate e mussarela"
        anterior = f"Prato #1, {nome}. Qual preço você escolhe para registrar no cardápio?"
        self.assertTrue(escolhas.perguntou_preco_do_prato(anterior, 1, nome))
        for resposta in ("faz pra mim isso, para eu ter lucro", "R$ 8,50 ou R$ 9,00", "será que R$ 8,50 serve?",
                         "> R$ 8,50", "8 reais se tiver lucro", "não escolha R$ 8,50", "2"):
            self.assertIsNone(escolhas.preco_para_registrar(resposta, anterior), resposta)
        for pergunta in (f"Prato #1, {nome}. Qual preço você prefere simular?", anterior + " Usou quanto de manteiga?", ""):
            self.assertIsNone(escolhas.preco_para_registrar("R$ 8,50", pergunta), pergunta)
        ambigua = anterior.replace("Prato #1", "Pratos #1 e #2")
        self.assertFalse(escolhas.perguntou_preco_do_prato(ambigua, 1, nome))

    def test_preco_natural_com_registro_herda_so_ficha_unica_da_pergunta_atual(self):
        nome = "Peito de frango assado com batata"
        anterior = (f"Calculei os cenários do prato #2, **{nome}**.\n"
                    "Conservador: R$ 4,50. Equilibrado: R$ 6,50. Confortável: R$ 9,00.\n"
                    "Qual preço por porção você escolhe para registrar esse prato no cardápio? Pode responder só com o valor em reais.")
        texto = "Escolho vender por R$ 6.50 por porção. Pode registrar."
        self.assertEqual(escolhas.preco_para_registrar(texto, anterior), 6.5)
        self.assertTrue(escolhas.pediu_registro_do_prato(texto, anterior, 2, nome))
        self.assertFalse(escolhas.pediu_registro_do_prato(texto, anterior, 1, "Outro prato"))
        self.assertIsNone(escolhas.preco_para_registrar(texto))
        self.assertIsNone(escolhas.preco_para_registrar(texto, "Qual ingrediente você quer consultar?"))
        ambigua = anterior.replace("prato #2", "pratos #1 e #2")
        self.assertFalse(escolhas.pediu_registro_do_prato(texto, ambigua, 2, nome))
        for ruim in ("Escolho vender por R$ 6.50 ou R$ 9 por porção. Pode registrar.",
                     "Escolho vender por R$ 6.50 por porção. Não pode registrar.",
                     "Escolho vender por R$ 6.50 por porção. Pode registrar o outro prato."):
            self.assertIsNone(escolhas.preco_para_registrar(ruim, anterior))

    def test_escolha_dos_dois_pratos_persiste_antes_do_modelo_responder(self):
        historico = [{"role": "assistant", "content": OPCOES}]
        store = self.iniciar_escolha_apos_pesquisa()
        store.registrar_compras([{"nome": "Mix de pimentas", "quantidade": 50, "unidade": "g", "preco_total": 1.25}])
        for _ in range(2):
            resposta = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="adicione esses 2 pratos pra mim")
            self.assertIn("Escolhas gravadas", resposta["context"])
        _, estado = api.tratar("GET", "/api/estado")
        pratos = estado["pratos_escolhidos"]
        self.assertEqual([p["nome"] for p in pratos], ["Filé de Peito Grelhado com Arroz Branco e Feijão Carioca",
                                                      "Almoço Prático: Arroz, Feijão e Frango Grelhado"])
        self.assertTrue(all(p["status"] == "selecionado" and p["cmv_total"] is None for p in pratos))
        self.assertEqual(estado["orcamento"]["saldo"], 78.75)
        self.assertEqual(estado["orcamento"]["reservado_cardapio"], 0)
        self.assertEqual(estado["compras_pagas"][0]["preco_total"], 1.25)
        self.assertEqual(store.reservas_ativas(), [])
        self.assertIn("https://example.com/frango", escolhas.preparar_turno(store, f"Quero continuar o prato escolhido #{pratos[0]['id']}", ""))

    def test_so_salva_escolha_inequivoca(self):
        for fala in ("adicione esses 3 pratos pra mim", "não adicione esses 2 pratos", "adicione os 2 pratos ao carrinho",
                     "adicione ao meu estoque", "como faço o prato 2?"):
            self.assertEqual(escolhas.escolhidas(fala, OPCOES), [])
        self.assertEqual([p["numero"] for p in escolhas.escolhidas("escolho a opção 2", OPCOES)], [2])

    def test_aceite_conversado_guarda_os_dois_pratos_da_lista(self):
        self.iniciar_escolha_apos_pesquisa()
        pergunta = "Informe o nome exato da receita para continuar."
        pedido = "sim, gostei desses 2 pratos, quero os 2"
        historico = [{"role": "assistant", "content": OPCOES_DE_FORNO_E_MASSA}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=pedido)
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=pedido)
        self.assertIn("Escolhas gravadas", resposta["context"])
        store = Store()
        ids = [p["id"] for p in store.pratos_em_andamento()]
        self.assertEqual(len(ids), 2)
        historico.extend([{"role": "user", "content": pedido}, {"role": "assistant", "content": pergunta}])
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="Quero ambos.")
        self.assertIn("Escolhas gravadas", resposta["context"])
        pratos = store.pratos_em_andamento()
        self.assertEqual([p["id"] for p in pratos], ids)
        self.assertEqual([(p["nome"], p["status"]) for p in pratos],
                         [("Arroz de forno super-rápido", "selecionado"), ("Macarrão bolonhesa", "selecionado")])
        self.assertEqual([p["fonte_url"] for p in pratos], ["https://example.com/receitas"] * 2)
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_escolha_coletiva_exige_intencao_e_quantidade_compativel(self):
        for pedido in ("Quero os dois!", "Gostei de ambos, vou querer as duas.", "Gostei desses 2 pratos e quero os dois.",
                       "Sim, pode guardar as duas opções.", "quero todas as receitas"):
            self.assertEqual([p["numero"] for p in escolhas.escolhidas(pedido, OPCOES_DE_FORNO_E_MASSA)], [1, 2], pedido)
        for pedido in ("Não quero os dois", "Gostei desses dois pratos", "quero as 3 opções", "quero os 2?",
                       "talvez eu queira os dois", "quero ambos, exceto o Macarrão bolonhesa", "quero os dois no carrinho"):
            self.assertEqual(escolhas.escolhidas(pedido, OPCOES_DE_FORNO_E_MASSA), [], pedido)
        self.assertEqual([p["numero"] for p in escolhas.escolhidas("escolho 2", OPCOES_DE_FORNO_E_MASSA)], [2])
        self.assertEqual(escolhas.escolhidas("quero os dois", "Só posso ajudar com outras ideias."), [])

    def test_retoma_lista_anterior_e_recupera_nome_na_conferencia(self):
        self.iniciar_escolha_apos_pesquisa()
        lista = """#### 1. Frango com Batata Fácil
Fonte: [Frango com Batata Fácil](https://example.com/frango-batata/)
**O que tem na despensa:** batata, cebola, alho.
**Precisa conferir ou comprar:** coxinhas da asa.

#### 2. Frango assado
Fonte: [Frango assado](https://example.com/frango-assado/)
**O que tem na despensa:** sal.
"""
        historico = [{"role": "assistant", "content": lista}]
        feedback = "gosto, e não vejo impedimento"
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=feedback)["context"]
        self.assertIn("Pergunte qual prefere entre 1. Frango com Batata Fácil; 2. Frango assado", contexto)
        self.assertEqual(Store().pratos(), [])
        historico += [{"role": "user", "content": feedback}, {"role": "assistant", "content": "Preciso do nome da receita ou do número do prato para continuar."}]
        selecao = "essa apresentada acima? 1. Frango com Batata Fácil"
        self.assertEqual(escolhas.escolhidas("2. Frango com Batata Fácil", lista), [])
        self.assertEqual(escolhas.escolhidas("como faço o prato 1?", lista), [])
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=selecao)
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=selecao)["context"]
        self.assertIn("Escolhas gravadas", contexto)
        prato, = Store().pratos()
        self.assertEqual(prato["nome"], "Frango com Batata Fácil")
        ficha = call("receita_conferir_ingredientes", tempo_preparo_min=30, porcoes=4, ingredientes=[
            {"nome": "coxinhas da asa de frango", "item_despensa": "Peito de frango", "quantidade": 12, "unidade": "un"},
            {"nome": "Batata", "quantidade": 600, "unidade": "g"},
        ])
        self.assertEqual(ficha["prato_id"], prato["id"])
        self.assertIn("é outro corte", ficha["pendencias"][0])
        self.assertEqual(Store().obter_receita(ficha["receita_id"])["fonte_url"], "https://example.com/frango-batata/")
        self.assertEqual(Store().reservas_ativas(), [])

    def test_prato_bloqueado_sem_alternativa_nao_recebe_cmv_nem_e_aprovado(self):
        prato = Store().selecionar_pratos([{"nome": "Frango com jambu"}])[0]
        ficha = call("receita_conferir_ingredientes", tempo_preparo_min=30, prato_id=prato["id"], porcoes=2, ingredientes=[
            {"nome": "jambu", "item_despensa": "", "quantidade": 100, "unidade": "g", "quantidade_compra": 25,
             "unidade_compra": "g", "preco_compra_estimado": 4},
        ])
        self.assertIsNone(ficha["resumo"]["cmv_previa"])
        self.assertIn("não cobre", ficha["pendencias"][0])
        self.assertIn("não foi aprovado", ficha["diga_a_dona_maria"])
        self.assertIn("Não dá para montar e registrar", ficha["diga_a_dona_maria"])
        self.assertIsNone(ficha["alternativa_no_estoque"])
        self.assertTrue(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["bloqueado"])
        self.assertTrue(call("cardapio_registrar", prato_id=prato["id"], preco_escolhido=20)["bloqueado"])
        self.assertEqual(Store().obter_prato(prato["id"])["status"], "selecionado")
        self.assertEqual(Store().orcamento()["saldo"], 80)

    def test_alternativa_so_troca_o_rascunho_depois_do_sim(self):
        store = Store()
        store.perfil_salvar({
            "equipamentos": {"fogao_bocas": 4, "frigideira": True, "forno": True, "panela_pressao": True,
                             "air_fryer": False, "liquidificador": True},
            "tecnicas": {"grelhar": "sim", "refogar": "sim"},
            "restricoes": {"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 90, "gas": "botijão", "energia": "estável"},
        })
        nome = "Frango grelhado com arroz e jambu"
        parecido = "Frango grelhado com arroz"
        original = store.selecionar_pratos([{"nome": nome}])[0]
        ingredientes = [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"},
                        {"nome": "Arroz branco", "quantidade": 300, "unidade": "g"}]
        requisitos = {"equipamentos_necessarios": ["fogão", "frigideira"], "tecnicas_necessarias": ["grelhar"]}
        call("receita_conferir_ingredientes", tempo_preparo_min=30, nome=parecido, porcoes=2, ingredientes=ingredientes, **requisitos)
        ficha = call("receita_conferir_ingredientes", tempo_preparo_min=30, prato_id=original["id"], porcoes=2, **requisitos,
                     ingredientes=[*ingredientes, {"nome": "Jambu", "quantidade": 100, "unidade": "g", "item_despensa": "",
                                                   "preco_compra_estimado": 100, "quantidade_compra": 100, "unidade_compra": "g"}])
        pergunta = ficha["diga_a_dona_maria"]
        self.assertIn(f"Quer trocar «{nome}» por «{parecido}»?", pergunta)
        historico = [{"role": "assistant", "content": pergunta}]
        painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="Não, obrigada.")
        self.assertEqual([p["nome"] for p in store.pratos_em_andamento()], [nome])
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="Sim.")
        self.assertIn("A troca foi confirmada", resposta["context"])
        self.assertEqual([(p["nome"], p["status"]) for p in store.pratos_em_andamento()], [(parecido, "selecionado")])
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)
