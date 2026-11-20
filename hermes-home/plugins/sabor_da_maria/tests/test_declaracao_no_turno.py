"""Uma ressalva literal atual deve prevalecer sobre o cadastro antigo antes do aceite."""

import json
from copy import deepcopy
from unittest.mock import Mock

from sabor_da_maria import cozinha, encerramento, falas, jornada, montagem, painel, precificacao, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class DeclaracaoNoTurnoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        equipamentos = {i.id: True for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE}
        equipamentos["fogao"] = 4
        self.store.perfil_salvar({"equipamentos": equipamentos,
                                 "tecnicas": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
                                 "restricoes": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120,
                                                "gas": "botijão", "energia": "estável"}})
        self.prato, = self.store.selecionar_pratos([{"nome": "Frango ao forno"}])
        self.store.salvar_receita("Frango ao forno", None, 4, {
            "ingredientes": [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}],
            "tempo_preparo_min": 50, "equipamentos_necessarios": ["forno", "forma"],
            "tecnicas_necessarias": ["assar"]}, "conferida", prato_id=self.prato["id"])

    def turno(self, texto, identificador="declaracao"):
        pergunta = f"Você aceita preparar o prato #{self.prato['id']}, Frango ao forno, nessa versão?"
        historico = [{"role": "assistant", "content": pergunta}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=texto)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id=identificador, user_message=texto,
                               conversation_history=historico)
        provedor = Mock(return_value="O modelo deve continuar")
        resposta = encerramento.executar({"messages": []}, provedor, session_id=SESSAO,
                                         turn_id=identificador, api_mode="chat_completions")
        return resposta, provedor

    def sem_compromissos(self):
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "selecionado")
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.carrinho_itens(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_aceite_com_negativa_atual_corrige_perfil_e_termina_no_impedimento(self):
        resposta, provedor = self.turno(f"Sim, aceito preparar o prato #{self.prato['id']}, mas não tenho forno.")
        provedor.assert_not_called()
        self.assertIs(self.store.perfil()["equipamentos"]["forno"], False)
        texto = resposta.choices[0].message.content
        self.assertIn("não pode ser aprovada", texto)
        self.assertIn("forno", texto)
        self.assertIn("adaptação", texto)
        self.assertNotIn("lucro", texto)
        self.sem_compromissos()

    def test_mesma_negativa_repetida_continua_factual_sem_novos_compromissos(self):
        mensagem = f"Não tenho forno para o prato #{self.prato['id']}."
        primeira, _ = self.turno(mensagem, "primeira")
        perfil = self.store.perfil()
        segunda, provedor = self.turno(mensagem, "segunda")
        provedor.assert_not_called()
        self.assertEqual(primeira.choices[0].message.content, segunda.choices[0].message.content)
        self.assertEqual(self.store.perfil(), perfil)
        self.sem_compromissos()

    def test_pedido_de_adaptacao_atualiza_perfil_e_deixa_o_modelo_procurar_versao(self):
        resposta, provedor = self.turno(f"Para o prato #{self.prato['id']}, não tenho forno, mas adapte a receita.")
        self.assertEqual(resposta, "O modelo deve continuar")
        provedor.assert_called_once()
        self.assertIs(self.store.perfil()["equipamentos"]["forno"], False)
        self.sem_compromissos()

    def test_tecnica_negada_e_gas_acabado_geram_impedimentos_especificos(self):
        for fala, grupo, chave, esperado in (("Não sei assar", "tecnicas", "assar", "nao"),
                                            ("Acabou o gás", "restricoes", "gas", "sem gás")):
            with self.subTest(fala=fala):
                # As condições não mencionadas pelo turno continuam válidas.
                self.store.perfil_salvar({"tecnicas": {"assar": "sim"}, "restricoes": {"gas": "botijão"}})
                resposta, provedor = self.turno(f"{fala} para o prato #{self.prato['id']}.", fala)
                provedor.assert_not_called()
                self.assertEqual(self.store.perfil()[grupo][chave], esperado)
                self.assertIn("não pode ser aprovada", resposta.choices[0].message.content)
                self.sem_compromissos()

    def test_fala_citada_ou_hipotetica_nao_invalida_o_cadastro(self):
        for frase in ('Ela disse "não tenho forno".', 'Se eu não tiver forno, como faço?'):
            with self.subTest(frase=frase):
                resposta, provedor = self.turno(f"Sobre o prato #{self.prato['id']}: {frase}", frase)
                self.assertEqual(resposta, "O modelo deve continuar")
                provedor.assert_called_once()
                self.assertIs(self.store.perfil()["equipamentos"]["forno"], True)
                self.sem_compromissos()

    def test_mudanca_sem_relacao_com_ficha_e_alvos_ambiguos_nao_encerram(self):
        resposta, provedor = self.turno(f"Não tenho esteira de sushi. Confira o prato #{self.prato['id']}.")
        self.assertEqual(resposta, "O modelo deve continuar")
        provedor.assert_called_once()
        self.assertIs(self.store.perfil()["equipamentos"]["esteira_sushi"], False)
        self.sem_compromissos()
        outro, = self.store.selecionar_pratos([{"nome": "Outro prato"}])
        resposta, provedor = self.turno(f"Não tenho forno para os pratos #{self.prato['id']} e #{outro['id']}.", "ambigua")
        self.assertEqual(resposta, "O modelo deve continuar")
        provedor.assert_called_once()
        self.sem_compromissos()


class RespostaCurtaDaCozinhaTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"],
                                  "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.store.perfil_salvar({"equipamentos": {"air_fryer": False}, "tecnicas": {"grelhar": "nao"},
                                 "restricoes": {"gas": "botijão", "energia": "estável", "espaco_geladeira": "bastante"}})
        self.prato, = self.store.selecionar_pratos([{"nome": "Pizza de frango"}])
        # Fonte simulada, com medidas completas, para isolar as respostas sobre a cozinha.
        original = {"nome": "Pizza de frango", "url": "https://receitas.exemplo/pizza-de-frango", "porcoes": 4,
                    "tempo_preparo_min": 60, "ingredientes": ["300 g de peito de frango", "500 g de farinha de trigo"],
                    "modo_preparo": "Refogue o frango, abra a massa na forma e asse no forno."}
        self.store.salvar_receita(original["nome"], original["url"], 4, {
            "fonte_verificada": original, "modo_preparo": original["modo_preparo"],
            "ingredientes": [{"nome": "Peito de frango", "quantidade": 300, "unidade": "g"},
                             {"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"}],
            "tempo_preparo_min": 60, "equipamentos_necessarios": ["fogão", "forno", "forma"],
            "tecnicas_necessarias": ["refogar", "assar"]}, "pendente", prato_id=self.prato["id"])
        self.historico = []

    def turno(self, texto, identificador, pergunta=None):
        if pergunta is None:
            pergunta = jornada.resumo(self.store)["pergunta"]["texto"]
        self.historico.append({"role": "assistant", "content": f"A ficha do prato #{self.prato['id']}, Pizza de frango, continua salva.\n\n{pergunta}"})
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=self.historico, user_message=texto)
        retorno = painel.ao_iniciar_turno(session_id=SESSAO, turn_id=identificador,
                                          conversation_history=self.historico, user_message=texto)
        self.assertIsNotNone(retorno)
        provedor = Mock(return_value="O modelo deve continuar")
        resposta = encerramento.executar({"messages": []}, provedor, session_id=SESSAO,
                                         turn_id=identificador, api_mode="chat_completions")
        self.historico.append({"role": "user", "content": texto})
        return resposta, provedor

    def sem_compromissos(self):
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "selecionado")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.carrinho_itens(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def preparar_bocas(self):
        resposta, provedor = self.turno("Tenho mais de 6 horas por cozinhada.", "tempo")
        provedor.assert_not_called()
        self.assertIn("Quantas bocas", resposta.choices[0].message.content)

    def test_seis_bocas_e_salvo_e_chat_avanca_sem_modelo_ou_formulario(self):
        self.preparar_bocas()
        pergunta = jornada.resumo(self.store)["pergunta"]["texto"]
        original = self.store.obter_receita(self.store.obter_prato(self.prato["id"])["receita_id"])
        resposta, provedor = self.turno("6", "bocas")
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["equipamentos"], {"air_fryer": False, "fogao": 6})
        texto = resposta.choices[0].message.content
        self.assertNotIn(pergunta, texto)
        self.assertIn(jornada.resumo(self.store)["pergunta"]["texto"], texto)
        self.assertEqual(texto.count("?"), 1)
        self.assertNotIn("recusados", texto)
        self.assertEqual(self.store.obter_receita(original["id"]), original)
        self.sem_compromissos()

    def test_pergunta_reformulada_tambem_salva_resposta_curta(self):
        self.preparar_bocas()
        resposta, provedor = self.turno("seis", "bocas-reformulada", "Seu fogão tem quantas bocas funcionando?")
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["equipamentos"]["fogao"], 6)
        self.assertEqual(resposta.choices[0].message.content.count("?"), 1)
        self.sem_compromissos()

    def test_botao_de_bocas_e_declaracao_explicita_resolvem_sem_historico_completo(self):
        self.preparar_bocas()
        pergunta = jornada.resumo(self.store)["pergunta"]
        mensagem = pergunta["opcoes"][-1]["mensagem"]
        resposta, provedor = self.turno(mensagem, "botao-bocas", "Vamos resolver sua cozinha.")
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["equipamentos"]["fogao"], 6)
        self.assertIn(jornada.resumo(self.store)["pergunta"]["texto"], resposta.choices[0].message.content)
        self.sem_compromissos()

    def test_zero_bocas_e_nao_a_forno_sao_impedimentos_sem_reservas(self):
        self.preparar_bocas()
        resposta, provedor = self.turno("0", "sem-bocas")
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["equipamentos"]["fogao"], 0)
        self.assertIn("adaptação", resposta.choices[0].message.content)
        self.sem_compromissos()
        self.store.perfil_salvar({"equipamentos": {"fogao": 6}})
        resposta, provedor = self.turno("Não", "sem-forno")
        provedor.assert_not_called()
        self.assertIs(self.store.perfil()["equipamentos"]["forno"], False)
        self.assertIn("adaptação", resposta.choices[0].message.content)
        self.sem_compromissos()

    def test_sim_a_tecnica_no_forno_nao_declara_possuir_forno(self):
        perfil = self.store.perfil()
        fala = falas.extrair([{"role": "assistant", "content": "Você sabe assar no forno neste preparo?"}], "Sei fazer")[-1]
        declarado = tools.atualizar_declaracoes_do_turno(self.store, fala)
        self.assertEqual(declarado, {"tecnicas": {"assar": "sim"}})
        self.assertEqual(self.store.perfil()["equipamentos"], perfil["equipamentos"])
        self.sem_compromissos()

    def test_contagem_repetida_e_bool_anterior_nao_apagam_o_numero(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": True}})
        pergunta = "Quantas bocas do seu fogão estão funcionando para este preparo?"
        fala = falas.extrair([{"role": "assistant", "content": pergunta}], "1")[-1]
        for _ in range(2):
            self.assertEqual(tools.atualizar_declaracoes_do_turno(self.store, fala), {"equipamentos": {"fogao": 1}})
            self.assertIs(type(self.store.perfil()["equipamentos"]["fogao"]), int)
        fala = falas.extrair([{"role": "assistant", "content": "Você tem fogão?"}], "Sim")[-1]
        tools.atualizar_declaracoes_do_turno(self.store, fala)
        self.assertIs(type(self.store.perfil()["equipamentos"]["fogao"]), int)
        self.sem_compromissos()

    def test_resposta_citada_ou_duvida_nao_viram_estado_de_cozinha(self):
        perfil = self.store.perfil()
        for texto in ('"Sim"', "Sim?", "Talvez", "Não sei"):
            with self.subTest(texto=texto):
                self.turno(texto, texto, "Você tem forno funcionando para este preparo?")
                self.assertEqual(self.store.perfil(), perfil)
                self.sem_compromissos()

    def test_contagem_nao_vira_resposta_de_equipamento_nem_aceite(self):
        self.preparar_bocas()
        perfil = self.store.perfil()
        resposta, provedor = self.turno("6", "pergunta-errada", "Você tem forno funcionando?")
        self.assertEqual(self.store.perfil(), perfil)
        self.assertEqual(resposta, "O modelo deve continuar")
        provedor.assert_called_once()
        self.sem_compromissos()

    def test_sim_a_formulario_ambiguo_nao_preenche_cozinha(self):
        self.preparar_bocas()
        perfil = self.store.perfil()
        self.turno("Sim", "formulario", "Você tem forno? Tem forma? Você sabe assar?")
        self.assertEqual(self.store.perfil(), perfil)
        self.sem_compromissos()

    def test_utensilios_da_ficha_sao_salvos_e_nao_repetem_a_pergunta(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": 6, "forno": True},
                                 "restricoes": {"tempo_por_cozinhada_min": 90}})
        self.store.salvar_receita(self.prato["nome"], None, 2, {
            "ingredientes": [{"nome": "Farinha de trigo", "quantidade": 300, "unidade": "g"}],
            "tempo_preparo_min": 30, "equipamentos_necessarios": ["tigela", "panela", "colher", "panela de pressão"],
            "montagem_com_estoque": True,
        }, "pendente", prato_id=self.prato["id"])
        anterior = "Você tem tigela?\nVocê tem panela?\nVocê tem colher?"
        for turno in ("tigela", "tigela-repetida"):
            resposta, provedor = self.turno("Tenho tigela.", turno, anterior)
            provedor.assert_not_called()
            self.assertNotIn("tigela", resposta.choices[0].message.content)
            self.assertIn("colher", resposta.choices[0].message.content)
            self.assertTrue(self.store.perfil()["equipamentos"]["tigela"])
            self.sem_compromissos()
        self.turno("Tenho panela.", "panela", "Você tem panela?")
        self.assertTrue(self.store.perfil()["equipamentos"]["panela"])
        self.assertNotIn("panela_pressao", self.store.perfil()["equipamentos"])
        pergunta = jornada.resumo(self.store)["pergunta"]
        self.assertIn("panela de pressão", pergunta["texto"])
        resposta, provedor = self.turno(pergunta["opcoes"][0]["mensagem"], "confirma-resto", pergunta["texto"])
        provedor.assert_not_called()
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.assertIn("aceita preparar", resposta.choices[0].message.content)
        self.assertTrue(self.store.perfil()["equipamentos"]["colher"])
        self.sem_compromissos()

    def test_resposta_curta_respeita_a_pergunta_do_utensilio(self):
        pergunta = "Você tem tigela para este preparo?"
        fala = falas.extrair([{"role": "assistant", "content": pergunta}], "Sim")[-1]
        out = tools.atualizar_declaracoes_do_turno(self.store, fala, equipamentos_da_ficha=("tigela", "colher"))
        self.assertEqual(out, {"equipamentos": {"tigela": True}})
        for pergunta in ("Você tem tigela e colher?", "Você tem tigela? Você tem colher?"):
            fala = falas.extrair([{"role": "assistant", "content": pergunta}], "Sim")[-1]
            self.assertEqual(tools.atualizar_declaracoes_do_turno(self.store, fala, equipamentos_da_ficha=("tigela", "colher")), {})
        self.assertNotIn("colher", self.store.perfil()["equipamentos"])

    def test_este_lote_nao_confirma_posse_de_esteira(self):
        fala = falas.Fala("Tenho energia suficiente para preparar e conservar este lote. Tenho forno funcionando.")
        out = tools.atualizar_declaracoes_do_turno(self.store, fala, equipamentos_da_ficha=("forno", "tigela"))
        self.assertEqual(out, {"equipamentos": {"forno": True}})
        self.assertNotIn("esteira_sushi", self.store.perfil()["equipamentos"])

    def test_tecnicas_da_ficha_avancam_sem_repetir_pergunta_generica(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": 6, "frigideira": True},
                                 "restricoes": {"tempo_por_cozinhada_min": 90}})
        self.store.salvar_receita(self.prato["nome"], None, 1, {
            "ingredientes": [{"nome": "Batata", "quantidade": 300, "unidade": "g"}],
            "tempo_preparo_min": 30, "equipamentos_necessarios": ["Frigideira"],
            "tecnicas_necessarias": ["Cozinhar batata", "Misturar", "Montar pizza"],
            "montagem_com_estoque": True,
        }, "pendente", prato_id=self.prato["id"])
        pergunta_antiga = "Você domina as técnicas necessárias para este prato?"
        mensagem = "Eu domino a técnica de Cozinhar batata."
        for rodada in ("primeira", "repetida"):
            resposta, provedor = self.turno(mensagem, rodada, pergunta_antiga)
            provedor.assert_not_called()
            self.assertEqual(self.store.perfil()["tecnicas"], {"grelhar": "nao", "cozinhar_batata": "sim"})
            texto = resposta.choices[0].message.content
            self.assertIn("você sabe misturar e montar pizza?", texto)
            self.assertNotIn(pergunta_antiga, texto)
            self.sem_compromissos()
        proxima = jornada.resumo(self.store)["pergunta"]
        resposta, provedor = self.turno("Sim", "resposta-vaga", proxima["texto"])
        provedor.assert_not_called()
        self.assertIn("Domino todas as técnicas", resposta.choices[0].message.content)
        self.assertEqual(jornada.resumo(self.store)["pergunta"], proxima)
        resposta, provedor = self.turno(proxima["opcoes"][0]["mensagem"], "confirma-as-restantes", proxima["texto"])
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["tecnicas"], {"grelhar": "nao", "cozinhar_batata": "sim", "misturar": "sim", "montar_pizza": "sim"})
        self.assertIn("aceita preparar", resposta.choices[0].message.content)
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.sem_compromissos()

    def test_tecnica_da_ficha_salva_negativa_e_pede_alternativa(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": 6, "frigideira": True},
                                 "restricoes": {"tempo_por_cozinhada_min": 90}})
        self.store.salvar_receita(self.prato["nome"], None, 1, {
            "ingredientes": [{"nome": "Batata", "quantidade": 300, "unidade": "g"}],
            "tempo_preparo_min": 30, "equipamentos_necessarios": ["Frigideira"],
            "tecnicas_necessarias": ["Cozinhar batata"],
        }, "pendente", prato_id=self.prato["id"])
        pergunta = jornada.resumo(self.store)["pergunta"]["texto"]
        self.assertEqual(pergunta, "Você sabe Cozinhar batata neste preparo?")
        resposta, provedor = self.turno("Não sei", "nao-domina", pergunta)
        provedor.assert_not_called()
        self.assertEqual(self.store.perfil()["tecnicas"]["cozinhar_batata"], "nao")
        self.assertIn("adaptação", resposta.choices[0].message.content)
        self.assertIsNone(jornada.resumo(self.store)["pergunta"])
        self.sem_compromissos()

    def test_respostas_curtas_concluem_conferencia_aceite_e_preco(self):
        self.preparar_bocas()
        self.turno("6", "bocas")
        for indice in range(10):
            estado = jornada.resumo(self.store)
            if estado["etapa"] == "aceite":
                break
            self.assertIsNotNone(estado["pergunta"], estado)
            self.assertIn(estado["pendencias"][0]["tipo"], {"equipamento_nao_informado", "tecnica_nao_informada"})
            resposta, provedor = self.turno("Sim", f"cozinha-{indice}")
            self.assertFalse(provedor.called, estado)
            self.assertEqual(resposta.choices[0].message.content.count("?"), 1)
            self.sem_compromissos()
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.assertIs(self.store.perfil()["equipamentos"]["air_fryer"], False)
        self.assertEqual(self.store.perfil()["tecnicas"]["grelhar"], "nao")
        self.assertEqual(self.store.perfil()["equipamentos"]["fogao"], 6)
        proposta = montagem.resposta_preparada(SESSAO, f"cozinha-{indice - 1}")
        self.assertIn("aceita preparar", proposta)
        analise, provedor = self.turno("Sim, aceito preparar essa versão.", "aceite", proposta)
        provedor.assert_not_called()
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "cmv_calculado")
        self.assertEqual(len(self.store.analises_preco()), 1)
        self.assertGreater(self.store.obter_prato(self.prato["id"])["cmv_porcao"], 0)
        self.assertTrue(self.store.reservas_ativas())
        self.assertEqual(self.store.orcamento()["saldo"], 80)
        registrado, provedor = self.turno("20,00", "preco", analise.choices[0].message.content)
        provedor.assert_not_called()
        self.assertIn("Registrei o prato", registrado.choices[0].message.content)
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "aceito")
        self.assertEqual(self.store.obter_prato(self.prato["id"])["preco_venda"], 20)
        self.assertEqual(self.store.orcamento()["saldo"], 80)


class MedidasDeUsoNoTurnoTests(StoreTestCase):
    turno = RespostaCurtaDaCozinhaTests.turno
    sem_compromissos = RespostaCurtaDaCozinhaTests.sem_compromissos

    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.prato, = self.store.selecionar_pratos([{"nome": "Pizza de frango"}])
        self.historico = []
        self.store.perfil_salvar({
            "equipamentos": {i.id: 6 if i.id == "fogao" else True for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE},
            "tecnicas": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
            "restricoes": {"tempo_por_cozinhada_min": 400, "gas": "botijão", "energia": "estável", "espaco_geladeira": "bastante"}})
        receita = {"nome": "Pizza de frango", "fonte_url": "https://receitas.exemplo/pizza-de-frango", "porcoes": 4}
        preparo = "Refogue o frango, abra a massa na forma e asse no forno."
        dados = {"ingredientes": [{"nome": "Peito de frango", "quantidade": 300, "unidade": "g"},
                                  {"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"}],
                 "fonte_verificada": {"nome": receita["nome"], "url": receita["fonte_url"], "porcoes": 4, "tempo_preparo_min": 60,
                                      "ingredientes": ["300 g de peito de frango", "500 g de farinha de trigo"], "modo_preparo": preparo},
                 "modo_preparo": preparo, "tempo_preparo_min": 60, "equipamentos_necessarios": ["fogão", "forno", "forma"],
                 "tecnicas_necessarias": ["refogar", "assar"]}
        for nome in ("cebola a gosto", "alho a gosto", "molho de tomate quanto baste"):
            dados["ingredientes"].append({"nome": nome})
            dados["fonte_verificada"]["ingredientes"].append(nome)
        # Cotação simulada: um kg de molho por R$ 10; não é uma compra realizada.
        dados["ingredientes"][-1].update(preco_compra_estimado=10, quantidade_compra=1, unidade_compra="kg", item_despensa=None)
        dados["ficha_original_da_fonte"] = True
        self.receita_id = self.store.salvar_receita(receita["nome"], receita["fonte_url"], receita["porcoes"], dados, "pendente", prato_id=self.prato["id"])
        self.original = deepcopy(self.store.obter_receita(self.receita_id)["dados"])
        self.estoque = deepcopy(self.store.ingredientes())
        self.perfil = deepcopy(self.store.perfil())

    def test_retomada_oferece_uma_medida_sem_alterar_fonte_ou_gastar(self):
        for indice in range(2):
            resposta, provedor = self.turno(f"Continue o prato #{self.prato['id']}.", f"retomar-{indice}", "Vamos continuar.")
            provedor.assert_not_called()
            texto = resposta.choices[0].message.content
            self.assertIn("100 g de cebola", texto)
            self.assertIn("não uma medida publicada", texto)
            self.assertNotIn("10 g de alho", texto)
            self.assertEqual(texto.count("?"), 1)
            dados = self.store.obter_receita(self.receita_id)["dados"]
            self.assertTrue(dados.pop("ingredientes_faltantes"))
            self.assertEqual(dados, self.original)
            self.sem_compromissos()

    def test_botao_sim_e_quantidade_digitada_avancam_ate_aceite_cmv_e_preco(self):
        pergunta = jornada.resumo(self.store)["pergunta"]
        self.assertEqual([o["rotulo"] for o in pergunta["opcoes"]], ["Usar 100 g"])
        resposta, provedor = self.turno(pergunta["opcoes"][0]["mensagem"], "cebola")
        provedor.assert_not_called()
        self.assertIn("10 g de alho", resposta.choices[0].message.content)
        self.sem_compromissos()
        self.turno("Sim", "alho")
        self.sem_compromissos()
        final, provedor = self.turno("0,15 kg", "molho")
        provedor.assert_not_called()
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        texto = final.choices[0].message.content
        self.assertIn("Medidas de uso locais confirmadas", texto)
        self.assertIn("aceita preparar", texto)
        dados = self.store.obter_receita(self.receita_id)["dados"]
        self.assertEqual(dados["fonte_verificada"], self.original["fonte_verificada"])
        self.assertEqual(dados["modo_preparo"], self.original["modo_preparo"])
        self.assertEqual(dados["ingredientes"][:2], self.original["ingredientes"][:2])
        self.assertEqual(dados["ingredientes"][-1]["preco_compra_estimado"], 10)
        self.assertEqual(self.store.perfil(), self.perfil)
        self.assertEqual(self.store.ingredientes(), self.estoque)
        self.sem_compromissos()
        analise, provedor = self.turno("Sim, aceito preparar essa versão.", "aceite-medidas", texto)
        provedor.assert_not_called()
        self.assertEqual(len(self.store.analises_preco()), 1)
        self.assertAlmostEqual(self.store.orcamento()["saldo"], 70)
        reservado = deepcopy(self.store.reservas_ativas())
        registrado, provedor = self.turno("20,00", "preco-medidas", analise.choices[0].message.content)
        provedor.assert_not_called()
        self.assertIn("Registrei o prato", registrado.choices[0].message.content)
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "aceito")
        self.assertEqual(self.store.reservas_ativas(), reservado)
        self.assertAlmostEqual(self.store.orcamento()["saldo"], 70)
        self.assertEqual(self.store.orcamento()["gasto_confirmado"], 0)

    def test_outra_quantidade_nomeada_e_repeticao_nao_preenchem_proximo_item(self):
        self.turno("Vou usar 80 g de cebola.", "outra-medida")
        receita = self.store.obter_receita(self.receita_id)
        self.assertEqual(receita["dados"]["ingredientes"][2]["quantidade"], 80)
        self.turno("Vou usar 80 g de cebola.", "repetida")
        self.assertEqual(self.store.obter_receita(self.receita_id), receita)
        self.assertIn("alho", jornada.resumo(self.store)["pergunta"]["texto"])
        self.sem_compromissos()

    def test_campo_da_ficha_vincula_medida_mesmo_quando_chat_citou_outro_prato(self):
        pergunta = jornada.resumo(self.store)["pergunta"]
        outro, = self.store.selecionar_pratos([{"nome": "Outro prato"}])
        token = pergunta["medida"]["cotacao"]
        mensagem = f"Vou usar 85,5 g de cebola neste lote. Continue o prato #{self.prato['id']}. Medidas {token}."
        resposta, provedor = self.turno(mensagem, "campo-medida", f"Quer revisar o prato #{outro['id']}?")
        provedor.assert_not_called()
        self.assertIn("alho", resposta.choices[0].message.content)
        ingredientes = self.store.obter_receita(self.receita_id)["dados"]["ingredientes"]
        self.assertEqual(ingredientes[2]["quantidade"], 85.5)
        self.assertIsNone(self.store.obter_prato(outro["id"])["receita_id"])
        self.sem_compromissos()

    def test_sim_sem_proposta_e_falas_ambiguas_nao_confirmam_medidas(self):
        casos = [("Sim", "Quanto você vai usar de cebola, alho e molho de tomate?"),
                 ("80 g", "Quantas bocas do seu fogão funcionam?"),
                 ("80", None), ("Não", None), ("Talvez", None), ("-80 g", None), ("0 g", None),
                 ('"80 g"', None), ("80 g?", None), ("80 g e 10 g", None),
                 ("80 g de cebola e 10 g de alho", None),
                 ("80 g de cebola. Continue o prato #999.", None)]
        for indice, (texto, pergunta) in enumerate(casos):
            with self.subTest(texto=texto):
                self.turno(texto, f"ambigua-{indice}", pergunta)
                self.assertEqual(self.store.obter_receita(self.receita_id)["dados"], self.original)
                self.sem_compromissos()

    def test_botao_obsoleto_e_atualizacao_concorrente_nao_sobrescrevem_ficha(self):
        pergunta = jornada.resumo(self.store)["pergunta"]
        mensagem = pergunta["opcoes"][0]["mensagem"]
        self.assertTrue(self.store.atualizar_medida_rascunho(self.prato["id"], self.receita_id, 2,
                                                         self.original["ingredientes"], 80, "g", "Vou usar 80 g de cebola."))
        self.assertFalse(self.store.atualizar_medida_rascunho(self.prato["id"], self.receita_id, 3,
                                                          self.original["ingredientes"], 10, "g", "10 g de alho"))
        atual = self.store.obter_receita(self.receita_id)
        self.turno(mensagem, "botao-obsoleto", pergunta["texto"])
        self.assertEqual(self.store.obter_receita(self.receita_id), atual)
        self.sem_compromissos()

    def test_reconferencia_e_cotacao_preservam_medidas_confirmadas(self):
        self.turno("Sim", "primeira-medida")
        anterior = self.store.obter_receita(self.receita_id)["dados"]
        retorno = json.loads(tools.receita_conferir_ingredientes({"prato_id": self.prato["id"]}, session_id=SESSAO))
        atual = self.store.obter_receita(retorno["receita_id"])["dados"]
        self.assertEqual(atual["medidas_de_uso_confirmadas"], anterior["medidas_de_uso_confirmadas"])
        self.assertEqual(atual["ingredientes"][2]["quantidade"], 100)
        self.assertEqual(atual["ingredientes"][-1]["preco_compra_estimado"], 10)
        self.assertIn("alho", retorno["pergunta_proxima"]["texto"])
        self.sem_compromissos()

    def test_medidas_vem_antes_de_reconfirmar_condicoes_do_lote(self):
        self.store.perfil_salvar({"restricoes": {"gas": "não garantido", "energia": "não garantida", "espaco_geladeira": "pouco"}})
        for texto, indice in (("Sim", "cebola"), ("Sim", "alho"), ("Sim", "molho")):
            self.assertIn("ingrediente_pendente", jornada.resumo(self.store)["pendencias"][0]["tipo"])
            self.turno(texto, indice)
        self.assertEqual(jornada.resumo(self.store)["pendencias"][0]["tipo"], "energia_a_confirmar")
        self.sem_compromissos()

    def test_ingrediente_sem_base_nao_recebe_quantidade_padrao(self):
        dados = deepcopy(self.original)
        dados["fonte_verificada"]["nome"] = "Salada de cebola"
        pendencia = {"tipo": "ingrediente_pendente", "detalhe": "cebola a gosto: a receita não traz a quantidade"}
        pergunta = montagem.pergunta_da_medida(self.prato, pendencia, dados)
        self.assertEqual(pergunta["opcoes"], [])
        self.assertIsNone(pergunta["medida"]["quantidade"])
        self.assertEqual(pergunta["texto"].count("?"), 1)
        self.store.salvar_receita("Salada de cebola", self.original["fonte_verificada"]["url"], 4,
                                 dados, "pendente", prato_id=self.prato["id"])
        receita = self.store.obter_receita(self.store.obter_prato(self.prato["id"])["receita_id"])
        fala = falas.extrair([{"role": "assistant", "content": pergunta["texto"]}], "80 g")[-1]
        self.assertTrue(montagem.salvar_medida_do_turno(self.store, self.prato, receita, fala, [pendencia]))
        self.assertEqual(self.store.obter_receita(receita["id"])["dados"]["ingredientes"][2]["quantidade"], 80)
        self.sem_compromissos()

    def test_confirmacao_de_medida_nunca_e_aceite_do_prato(self):
        pergunta = jornada.resumo(self.store)["pergunta"]
        for texto in (pergunta["opcoes"][0]["mensagem"],
                      f"Confirmo 0,1 kg de cebola. Continue o prato #{self.prato['id']}.",
                      f"Confirmo a medida de cebola para o prato #{self.prato['id']}."):
            with self.subTest(texto=texto):
                fala = falas.extrair([], texto)[-1]
                self.assertIsNone(precificacao.aceite_da_receita(self.prato["id"], self.prato["nome"], [fala]))
        self.sem_compromissos()

    def test_medidas_confirmadas_exigem_aceite_final_mesmo_com_escolha_para_usar(self):
        prato = self.store.obter_prato(self.prato["id"])
        prato["dados"]["selecao"] = {"usar_quando_viavel": True, "fonte_url": self.original["fonte_verificada"]["url"]}
        receita = self.store.obter_receita(self.receita_id)
        self.assertTrue(tools._pediu_para_usar_versao(prato, receita))
        self.turno("Sim", "cebola-confirmada")
        receita = self.store.obter_receita(self.receita_id)
        self.assertFalse(tools._pediu_para_usar_versao(prato, receita))
        self.sem_compromissos()
