from sabor_da_maria import api, escolhas, falas, painel
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


class EscolhasTests(StoreTestCase):
    def test_conferencia_aceite_e_preco_avancam_o_mesmo_prato(self):
        store = Store()
        diz(FALA_COZINHA)
        perfil = call("perfil_cozinha_salvar", equipamentos={"fogão": 4, "forno": True, "panela de pressão": True,
                      "air fryer": False, "liquidificador": True}, tecnicas={"refogado": "sim"},
                      restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"})
        self.assertTrue(perfil["completo"])
        prato, outro = store.selecionar_pratos([{"nome": "Frango em cubos"}, {"nome": "Frango com arroz"}])
        prato_id = prato["id"]
        self.assertFalse(call("receita_conferir_ingredientes", porcoes=3, ingredientes=["peito de frango"])["success"])
        diz(f"Quero continuar o prato escolhido #{prato_id} e conferir a ficha.")
        ficha = call("receita_conferir_ingredientes", porcoes=3,
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
        self.assertEqual(store.reservas_ativas(), [])
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

    def test_escolha_dos_dois_pratos_persiste_antes_do_modelo_responder(self):
        historico = [{"role": "assistant", "content": OPCOES}]
        store = Store()
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

    def test_retoma_lista_anterior_e_recupera_nome_na_conferencia(self):
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
        ficha = call("receita_conferir_ingredientes", porcoes=4, ingredientes=[
            {"nome": "coxinhas da asa de frango", "item_despensa": "Peito de frango", "quantidade": 12, "unidade": "un"},
            {"nome": "Batata", "quantidade": 600, "unidade": "g"},
        ])
        self.assertEqual(ficha["prato_id"], prato["id"])
        self.assertIn("é outro corte", ficha["pendencias"][0])
        self.assertEqual(Store().obter_receita(ficha["receita_id"])["fonte_url"], "https://example.com/frango-batata/")
        self.assertEqual(Store().reservas_ativas(), [])

    def test_prato_bloqueado_sem_alternativa_nao_recebe_cmv_nem_e_aprovado(self):
        prato = Store().selecionar_pratos([{"nome": "Frango com jambu"}])[0]
        ficha = call("receita_conferir_ingredientes", prato_id=prato["id"], porcoes=2, ingredientes=[
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
        call("receita_conferir_ingredientes", nome=parecido, porcoes=2, ingredientes=ingredientes, **requisitos)
        ficha = call("receita_conferir_ingredientes", prato_id=original["id"], porcoes=2, **requisitos,
                     ingredientes=[*ingredientes, {"nome": "Jambu", "quantidade": 100, "unidade": "g", "item_despensa": ""}])
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
