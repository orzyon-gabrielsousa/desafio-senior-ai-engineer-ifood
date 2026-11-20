"""Restrições operacionais não podem atravessar aceite, CMV ou reserva (§2.2)."""

import json

from sabor_da_maria import falas, jornada, montagem, painel, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase, diz


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args, session_id=SESSAO))


class RestricoesOperacionaisTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.perfil_salvar({
            "equipamentos": {"fogao": 4, "forno": True, "panela_pressao": True, "air_fryer": True,
                             "liquidificador": True, "frigideira": True, "geladeira": True},
            "tecnicas": {"refogar": "sim", "grelhar": "sim"},
            "restricoes": {"tempo_por_cozinhada_min": 60, "espaco_geladeira": "médio",
                           "gas": "botijão", "energia": "estável"},
        })

    def conferir(self, **extras):
        dados = {"nome": "Frango em cubos", "porcoes": 2, "tempo_preparo_min": 30,
                 "equipamentos_necessarios": ["fogão a gás"], "tecnicas_necessarias": ["refogar"],
                 "ingredientes": [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}]}
        return call("receita_conferir_ingredientes", **{**dados, **extras})

    def mudar_rotina(self, **valores):
        self.store.perfil_salvar({"restricoes": valores})

    def test_receita_de_duas_horas_nao_cabe_em_dez_minutos_mesmo_com_aceite(self):
        self.mudar_rotina(tempo_por_cozinhada_min=10)
        prato, = self.store.selecionar_pratos([{"nome": "Frango em cubos"}])
        ficha = self.conferir(prato_id=prato["id"], tempo_preparo_min=120)
        self.assertEqual(ficha["veredito"], "inviavel_por_enquanto")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "tempo_insuficiente")
        self.assertIn("120 min", ficha["diga_a_dona_maria"])
        self.assertNotIn("ingredientes disponíveis", ficha["diga_a_dona_maria"])
        diz(f"Confirmo o prato #{prato['id']} nessa versão.")
        retorno = painel.ao_iniciar_turno(session_id=SESSAO, user_message=f"Confirmo o prato #{prato['id']} nessa versão.")
        self.assertIn("120 min", retorno["context"])
        self.assertEqual(self.store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertFalse(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_duracao_ausente_ou_invalida_pede_so_a_informacao_restante(self):
        for tempo in (None, 0, -10, float("nan")):
            with self.subTest(tempo=tempo):
                ficha = self.conferir(tempo_preparo_min=tempo)
                self.assertEqual(ficha["veredito"], "precisa_confirmar")
                self.assertEqual([b["tipo"] for b in ficha["bloqueios"]], ["tempo_da_receita_pendente"])
                self.assertNotIn("ingredientes disponíveis", ficha["diga_a_dona_maria"])
                self.assertFalse(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])
        self.assertEqual(self.conferir(tempo_preparo_min=30)["veredito"], "viavel")

    def test_registro_reavalia_restricao_alterada_depois_da_analise(self):
        ficha = self.conferir()
        cmv = call("prato_calcular_cmv", receita_id=ficha["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        prato_id = cmv["prato_id"]
        diz(f"Confirmo o prato #{prato_id} nessa versão.")
        proposta = call("preco_sugerir", prato_id=prato_id, aceite_prato=f"Confirmo o prato #{prato_id} nessa versão.")
        self.assertTrue(proposta["analise_salva"], proposta)
        self.mudar_rotina(tempo_por_cozinhada_min=10)
        mensagem = f"Escolho vender o prato #{prato_id} por R$ 10,00 por porção. Registre no cardápio."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=mensagem)
        registro = call("cardapio_registrar", prato_id=prato_id, preco_escolhido=10)
        self.assertFalse(registro["success"], registro)
        self.assertIn("tempo_insuficiente", [b["tipo"] for b in registro["bloqueios"]])
        painel.ao_iniciar_turno(session_id=SESSAO, user_message=mensagem)
        self.assertNotEqual(self.store.obter_prato(prato_id)["status"], "aceito")
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_gas_faltante_bloqueia_aparelho_a_gas_e_pede_fonte_do_generico(self):
        self.mudar_rotina(gas="sem gás")
        ficha = self.conferir()
        self.assertEqual(ficha["veredito"], "inviavel_por_enquanto")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "gas_indisponivel")
        generica = self.conferir(equipamentos_necessarios=["fogão"])
        self.assertEqual(generica["veredito"], "precisa_confirmar")
        self.assertEqual(generica["bloqueios"][0]["tipo"], "gas_a_confirmar")
        eletrica = self.conferir(equipamentos_necessarios=["forno elétrico"])
        self.assertEqual(eletrica["veredito"], "viavel", eletrica)

    def test_energia_intermitente_pede_alternativa_so_quando_necessaria(self):
        self.mudar_rotina(energia="cai às vezes")
        ficha = self.conferir(equipamentos_necessarios=["air fryer"])
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "energia_a_confirmar")
        self.mudar_rotina(energia="sem energia")
        ficha = self.conferir(equipamentos_necessarios=["air fryer"])
        self.assertEqual(ficha["veredito"], "inviavel_por_enquanto")
        # Arroz seco no fogão a gás não depende de eletricidade nem de geladeira.
        arroz = self.conferir(nome="Arroz da casa", ingredientes=[{"nome": "Arroz", "quantidade": 100, "unidade": "g"}])
        self.assertEqual(arroz["veredito"], "viavel", arroz)

    def test_pouco_espaco_exige_confirmacao_do_lote_perecivel(self):
        self.mudar_rotina(espaco_geladeira="pouco")
        ficha = self.conferir()
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "refrigeracao_a_confirmar")
        self.mudar_rotina(espaco_geladeira="sem espaço")
        self.assertEqual(self.conferir()["veredito"], "inviavel_por_enquanto")
        arroz = self.conferir(nome="Arroz da casa", ingredientes=[{"nome": "Arroz", "quantidade": 100, "unidade": "g"}])
        self.assertEqual(arroz["veredito"], "viavel", arroz)

    def test_requisito_da_base_tambem_confere_fornecimento(self):
        self.store.perfil_salvar({"equipamentos": {"forma": True}, "tecnicas": {"massa_fermentada": "sim", "assar": "sim"}})
        self.mudar_rotina(gas="falta às vezes")
        # Mesmo que o modelo omita o forno, a ficha da pizza exige esse aparelho.
        ficha = self.conferir(nome="Pizza de calabresa", equipamentos_necessarios=[], tecnicas_necessarias=[],
                              ingredientes=[{"nome": "Farinha de trigo", "quantidade": 100, "unidade": "g"}])
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual([b["tipo"] for b in ficha["bloqueios"]], ["gas_a_confirmar"])

    def test_variantes_de_risco_e_falta_de_fornecimento_nao_passam_como_sim(self):
        self.mudar_rotina(gas="botijão com risco de acabar", energia="estável")
        self.assertEqual(self.conferir()["bloqueios"][0]["tipo"], "gas_a_confirmar")
        self.mudar_rotina(gas="botijão", energia="oscila durante o dia")
        self.assertEqual(self.conferir(equipamentos_necessarios=["air fryer"])["bloqueios"][0]["tipo"], "energia_a_confirmar")
        self.mudar_rotina(energia="sem luz")
        self.assertEqual(self.conferir(equipamentos_necessarios=["air fryer"])["bloqueios"][0]["tipo"], "energia_indisponivel")

    def test_confirmacao_especifica_resolve_pouco_espaco_sem_alterar_a_cozinha(self):
        self.mudar_rotina(espaco_geladeira="pouco")
        evidencia = "Sim, cabe este lote na minha geladeira."
        diz(evidencia)
        ficha = self.conferir(confirmacoes_operacionais={"espaco_geladeira": evidencia})
        self.assertEqual(ficha["veredito"], "viavel", ficha)
        self.assertTrue(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])
        self.assertEqual(self.store.perfil()["restricoes"]["espaco_geladeira"], "pouco")

        # A mesma confirmação não vale para outro lote, mesmo com o mesmo nome.
        receita = self.store.obter_receita(ficha["receita_id"])
        receita["porcoes"] = 4
        atual = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
        self.assertEqual(atual["veredito"], "precisa_confirmar")
        receita["porcoes"] = 2
        receita["dados"]["ingredientes"][0]["quantidade"] = 600
        atual = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
        self.assertEqual(atual["veredito"], "precisa_confirmar")
        self.mudar_rotina(energia="cai às vezes")
        self.assertFalse(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])

    def test_aceite_generico_e_evidencia_inventada_nao_liberam_restricao(self):
        self.mudar_rotina(espaco_geladeira="pouco")
        for fala, evidencia in (("Sim", "Sim"), ("Aceito o prato", "Sim, cabe este lote na minha geladeira."),
                                 ("Não cabe este lote na geladeira", "Não cabe este lote na geladeira")):
            with self.subTest(fala=fala):
                diz(fala)
                ficha = self.conferir(confirmacoes_operacionais={"espaco_geladeira": evidencia})
                self.assertTrue(ficha["success"], ficha)  # A ficha é guardada, sem liberar a condição.
                self.assertEqual(ficha["veredito"], "precisa_confirmar")
                self.assertEqual([c["chave"] for c in ficha["confirmacoes_recusadas"]], ["espaco_geladeira"])
                self.assertFalse(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])
        self.mudar_rotina(tempo_por_cozinhada_min=10)
        evidencia = "Sim, cabe este lote na minha geladeira."
        diz(evidencia)
        ficha = self.conferir(tempo_preparo_min=120, confirmacoes_operacionais={"espaco_geladeira": evidencia})
        self.assertEqual(ficha["veredito"], "inviavel_por_enquanto")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "tempo_insuficiente")

    def test_confirmacao_superflua_nao_reabre_condicao_ja_suficiente(self):
        ficha = self.conferir(confirmacoes_operacionais={"gas": "Confirmação inventada pelo modelo"})
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["veredito"], "viavel")
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual(receita["dados"]["confirmacoes_operacionais"], [])
        self.assertTrue(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])

    def test_so_confirma_pendencia_real_e_ignora_extras_do_modelo(self):
        self.mudar_rotina(espaco_geladeira="pouco")
        evidencia = "Sim, cabe este lote na minha geladeira."
        diz(evidencia)
        ficha = self.conferir(confirmacoes_operacionais={"espaco_geladeira": evidencia, "gas": "fala inexistente"})
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["veredito"], "viavel")
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual([c["chave"] for c in receita["dados"]["confirmacoes_operacionais"]], ["espaco_geladeira"])
        self.assertTrue(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])

    def turno_da_ficha(self, turno, prato_id, pergunta, mensagem):
        historico = [{"role": "assistant", "content": f"A ficha do prato #{prato_id} continua salva.\n\n{pergunta}"}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=mensagem)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, conversation_history=historico, user_message=mensagem)
        return montagem.resposta_preparada(SESSAO, turno)

    def test_respostas_do_lote_atualizam_pergunta_do_chat_e_da_tela(self):
        self.mudar_rotina(gas=None, energia="cai às vezes", espaco_geladeira="pouco")
        prato, = self.store.selecionar_pratos([{"nome": "Frango em cubos"}])
        prato_id = prato["id"]
        ficha = self.conferir(prato_id=prato_id)
        self.assertEqual(jornada.resumo(self.store)["pendencias"][0]["tipo"], "energia_a_confirmar")
        energia = self.turno_da_ficha("energia-do-lote", prato_id,
                                   "Você consegue garantir energia suficiente para preparar e conservar este lote?",
                                   f"Tenho energia suficiente para preparar e conservar este lote. Continue o prato #{prato_id}.")
        pergunta_gas = jornada.resumo(self.store)["pergunta"]["texto"]
        self.assertIn(pergunta_gas, energia)

        # A conversa já mostrava geladeira, mas o botão antigo ainda enviava gás.
        resposta = self.turno_da_ficha("gas-do-lote", prato_id,
                                      "Você tem espaço na geladeira para os ingredientes e as porções deste lote?",
                                      f"Tenho gás suficiente para preparar este lote. Continue o prato #{prato_id}.")
        pergunta_geladeira = jornada.resumo(self.store)["pergunta"]["texto"]
        self.assertIn("geladeira", pergunta_geladeira)
        self.assertIn(pergunta_geladeira, resposta)
        self.turno_da_ficha("geladeira-do-lote", prato_id, pergunta_geladeira,
                           f"Há espaço suficiente na geladeira para os ingredientes e as porções deste lote. Continue o prato #{prato_id}.")
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.assertIsNone(self.store.perfil()["restricoes"].get("gas"))
        self.assertEqual(self.store.perfil()["restricoes"]["energia"], "cai às vezes")
        self.assertEqual(self.store.perfil()["restricoes"]["espaco_geladeira"], "pouco")

        self.store.perfil_salvar({"equipamentos": {"fogao": 5}})
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        conferida = self.conferir(prato_id=prato_id)
        receita = self.store.obter_receita(conferida["receita_id"])
        self.assertEqual({c["chave"] for c in receita["dados"]["confirmacoes_operacionais"]},
                         {"gas", "energia", "espaco_geladeira"})
        self.assertEqual(conferida["veredito"], "viavel")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)
        self.assertNotEqual(ficha["receita_id"], conferida["receita_id"])

    def test_pergunta_agrupada_mantem_confirmacoes_literais_e_para_no_impedimento(self):
        prato = {"id": 7, "nome": "Pizza de frango"}
        gas = {"tipo": "gas_a_confirmar", "detalhe": "O gás não está garantido para a receita."}
        forno = {"tipo": "equipamento_nao_informado", "detalhe": "forno: você ainda não disse se tem"}
        alternativa = {"tipo": "equipamento_nao_informado", "detalhe": "forno ou air fryer: você ainda não disse se tem"}
        tec = {"tipo": "tecnica_nao_informada", "detalhe": "assar: você ainda não disse se domina"}
        dados = {"montagem_com_estoque": True}

        pergunta = jornada.pergunta_pendente(prato, [gas, forno, tec], dados)
        self.assertTrue(pergunta["confirmacoes_agrupadas"])
        opcao, = pergunta["opcoes"]
        self.assertEqual(opcao["mensagem"], "Tenho gás suficiente para preparar este lote. "
                         "Tenho forno funcionando. Sei assar no forno. Continue o prato #7.")
        self.assertEqual(set(falas.condicoes_do_lote(opcao["mensagem"])), {"gas"})
        self.assertFalse(jornada.perguntou_pendencia(
            "Você consegue garantir gás suficiente para preparar este lote?", pergunta, "gas_a_confirmar"))
        self.assertTrue(jornada.perguntou_pendencia(pergunta["texto"], pergunta, "gas_a_confirmar"))

        for bloqueios, ficha in (([gas, forno, tec], {}), ([gas, alternativa, tec], dados), ([gas], dados)):
            self.assertFalse(jornada.pergunta_pendente(prato, bloqueios, ficha).get("confirmacoes_agrupadas"))
        negativa = {"tipo": "gas_indisponivel", "detalhe": "Você informou falta de gás para este lote."}
        self.assertIsNone(jornada.pergunta_pendente(prato, [negativa, forno, tec], dados))

    def test_negativa_para_o_lote_e_impedimento_sem_repetir_pergunta(self):
        self.mudar_rotina(gas=None)
        prato, = self.store.selecionar_pratos([{"nome": "Frango em cubos"}])
        prato_id = prato["id"]
        self.conferir(prato_id=prato_id)
        pergunta = jornada.resumo(self.store)["pergunta"]["texto"]
        resposta = self.turno_da_ficha("nega-gas-do-lote", prato_id, pergunta,
                                      f"Não consigo garantir gás suficiente para preparar este lote. Continue o prato #{prato_id}.")
        estado = jornada.resumo(self.store)
        self.assertEqual(estado["pendencias"][0]["tipo"], "gas_indisponivel")
        self.assertIsNone(estado["pergunta"])
        self.assertNotIn(pergunta, resposta)
        self.assertIn("adaptação", resposta)
        self.assertIsNone(self.store.perfil()["restricoes"].get("gas"))

    def test_duas_perguntas_reformuladas_esgotam_a_mesma_pendencia(self):
        self.mudar_rotina(espaco_geladeira="pouco")
        prato, = self.store.selecionar_pratos([{"nome": "Frango em cubos"}])
        self.conferir(prato_id=prato["id"])
        historico = [
            {"role": "assistant", "content": "Você tem espaço na geladeira para os ingredientes e as porções deste lote?"},
            {"role": "user", "content": "Não sei."},
            {"role": "assistant", "content": "Você consegue guardar os ingredientes e o lote na geladeira?"},
        ]
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id="duas-perguntas", conversation_history=historico,
                              user_message="Não sei.")
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "duas-perguntas"))
        estado = jornada.resumo(self.store)
        self.assertTrue(estado["pergunta_esgotada"])
        self.assertIsNone(estado["pergunta"])
        self.assertEqual(estado["pendencias"][0]["tipo"], "refrigeracao_a_confirmar")
