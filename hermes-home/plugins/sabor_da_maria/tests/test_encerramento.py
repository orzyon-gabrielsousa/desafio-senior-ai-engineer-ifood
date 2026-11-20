"""O modelo descobre receitas; resultados conferidos encerram sem outra geração."""

import copy
from unittest.mock import Mock

from sabor_da_maria import encerramento, falas, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class EncerramentoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"], "estado": "SP", "cidade": "São Paulo"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.store.cozinha_salvar({
            "equipamentos": {"fogao": 6, "forno": True, "forma": True, "panela_grande": True,
                             "panela_pressao": True, "air_fryer": True, "liquidificador": True},
            "habilidades": {"arroz_feijao": "sim", "assar": "sim", "refogar": "sim"},
            "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120,
                       "gas": "botijão", "energia": "estável"},
        })

    def executar(self, turno, *, modo="chat_completions", sessao=SESSAO):
        request = {"model": "qwen3-8b", "messages": [{"role": "user", "content": "mensagem"}]}
        provedor = Mock(return_value="resposta do provedor")
        resposta = encerramento.executar(request, provedor, session_id=sessao, turn_id=turno,
                                         api_mode=modo, model="qwen3-8b")
        return resposta, provedor, request

    def turno(self, texto, anterior, identificador):
        historico = [{"role": "assistant", "content": anterior}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=texto)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id=identificador,
                               conversation_history=historico, user_message=texto)

    def test_descoberta_inicial_continua_pesquisando_sem_ficha_pronta(self):
        self.turno("monte um prato para meu cardápio", "O que você quer cozinhar?", "pesquisa")
        resposta, provedor, request = self.executar("pesquisa")
        self.assertEqual(resposta, "resposta do provedor")
        provedor.assert_called_once_with(request)
        self.assertEqual(self.store.receitas(), [])
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_conducao_executa_leituras_delegadas_antes_de_pedir_decisao(self):
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"], "estado": "SP", "cidade": "São Paulo"})
        self.turno("Quero sua ajuda. Pesquise uma receita real e monte uma proposta com o estoque disponível.", "", "conducao")
        nomes = ["despensa_listar", "web_search", "web_extract", "receita_conferir_ingredientes", "cardapio_registrar"]
        request = {"model": "qwen3-8b", "tools": [{"type": "function", "function": {"name": n}} for n in nomes]}
        provedor = Mock(return_value="resultado")
        encerramento.executar(request, provedor, session_id=SESSAO, turn_id="conducao", api_mode="chat_completions")
        enviado = provedor.call_args.args[0]
        self.assertEqual(enviado["tool_choice"]["function"]["name"], "despensa_listar")
        self.assertNotIn("tool_choice", request)
        pesquisa.depois_da_ferramenta(tool_name="despensa_listar", result='{"success":true}', session_id=SESSAO)
        provedor.reset_mock()
        encerramento.executar(request, provedor, session_id=SESSAO, turn_id="conducao", api_mode="chat_completions")
        self.assertEqual(provedor.call_args.args[0]["tool_choice"]["function"]["name"], "web_search")
        pesquisa.depois_da_ferramenta(tool_name="web_search", result='{"error":"rede indisponível"}', session_id=SESSAO)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_search")
        pesquisa.depois_da_ferramenta(tool_name="web_search", result='{"error":"rede indisponível"}', session_id=SESSAO)
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(self.store.pratos(), [])
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_conducao_nao_forca_ferramenta_ausente_nem_muda_pedido_paralelo(self):
        pesquisa.marcar_turno(SESSAO, "monte um prato para meu cardápio", conduzida=True)
        _, provedor, request = self.executar("sem-toolset")
        provedor.assert_called_once_with(request)
        self.turno("Qual é o saldo?", "", "saldo")
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))

    def test_qwen_local_gera_argumentos_com_prefixo_sem_alterar_historico_original(self):
        pesquisa.marcar_turno(SESSAO, "monte um prato para meu cardápio", conduzida=True)
        request = {"model": "qwen3-8b", "messages": [{"role": "user", "content": "Conduza meu cardápio."}],
                   "tools": [{"type": "function", "function": {"name": n}} for n in ["despensa_listar", "web_search"]]}
        antes = copy.deepcopy(request)
        resultado = object()
        provedor = Mock(return_value=resultado)
        resposta = encerramento.executar(request, provedor, session_id=SESSAO, turn_id="prefixo",
            api_mode="chat_completions", model="qwen3-8b", provider="custom", base_url="http://inference:8000/v1")
        self.assertIs(resposta, resultado)
        enviado = provedor.call_args.args[0]
        self.assertEqual(enviado["tools"], [request["tools"][0]])
        self.assertEqual(enviado["tool_choice"], "required")
        self.assertEqual(enviado["messages"][-1], {"role": "assistant", "content": '<tool_call>\n'})
        self.assertEqual(request, antes)

    def test_adaptacao_delegada_exige_ficha_completa_sem_mutar_schema_original(self):
        prato, = self.store.selecionar_pratos([{"nome": "Prato de teste"}])
        receita_id = self.store.salvar_receita("Prato de teste", None, 4, {"ingredientes": [{"nome": "Arroz", "quantidade": 300, "unidade": "g"}]},
                                              "conferida", prato_id=prato["id"])
        pesquisa.conduzir_adaptacao(SESSAO, self.store.obter_prato(prato["id"]), self.store.obter_receita(receita_id))
        pesquisa.depois_da_ferramenta(tool_name="despensa_listar", result='{"success":true}', session_id=SESSAO)
        request = {"model": "qwen3-8b", "tools": [{"type": "function", "function": tools.SCHEMAS["receita_conferir_ingredientes"]}]}
        antes = copy.deepcopy(request)
        provedor = Mock()
        encerramento.executar(request, provedor, session_id=SESSAO, turn_id="adaptacao",
                              api_mode="chat_completions", model="qwen3-8b", provider="custom", base_url="http://inference:8000/v1")
        enviado = provedor.call_args.args[0]
        obrigatorios = enviado["tools"][0]["function"]["parameters"]["required"]
        self.assertTrue({"ingredientes", "ingredientes_omitidos", "adaptacao", "modo_preparo"} <= set(obrigatorios))
        self.assertEqual(request, antes)
        pesquisa.marcar_turno(SESSAO, None)
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))

    def test_prefixo_restrito_ao_backend_conhecido_e_json_gerado_inteiro_pelo_modelo(self):
        self.assertTrue(encerramento._prefixo_qwen("custom", "http://inference:8000/v1/", "qwen3-8b"))
        for provider, url, model in [("openai", "http://inference:8000/v1", "qwen3-8b"),
                                     ("custom", "https://outro.exemplo/v1", "qwen3-8b"),
                                     ("custom", "http://inference:8000/v1", "outro-modelo")]:
            self.assertFalse(encerramento._prefixo_qwen(provider, url, model))
        self.assertEqual(encerramento._inicio_da_chamada(), '<tool_call>\n')
        self.assertNotIn("{", encerramento._inicio_da_chamada())

    def test_pendencia_corrigivel_nao_encerra_o_modelo(self):
        montagem.iniciar_turno(SESSAO, "pendente")
        montagem.registrar_resposta(SESSAO, ["Falta conferir uma unidade da compra."], prioridade=10)
        resposta, provedor, request = self.executar("pendente")
        self.assertEqual(resposta, "resposta do provedor")
        provedor.assert_called_once_with(request)

    def test_proposta_aceite_e_registro_usam_os_resultados_atuais_sem_nova_inferencia(self):
        self.turno("monte um prato equivalente com esses mesmos ingredientes",
                   "O Arroz de forno super-rápido ficou pendente. Quer uma adaptação?", "proposta")
        proposta, provedor, _ = self.executar("proposta")
        provedor.assert_not_called()
        texto = proposta.choices[0].message.content
        self.assertIn("Arroz de forno com frango, tomate e mussarela", texto)
        self.assertIn("aceita preparar o prato", texto)
        self.assertEqual(texto.count("?"), 1)
        self.assertEqual(proposta.choices[0].finish_reason, "stop")
        self.assertEqual(proposta.usage.total_tokens, 0)
        prato, = self.store.pratos()
        self.assertEqual(self.store.reservas_ativas(), [])

        self.turno(f"Aceito preparar o prato #{prato['id']} nessa versão.", texto, "aceite")
        analise, provedor, _ = self.executar("aceite")
        provedor.assert_not_called()
        texto = analise.choices[0].message.content
        self.assertIn("10%", texto)
        self.assertIn("Qual preço por porção", texto)
        self.assertEqual(len(self.store.analises_preco()), 1)
        reservas = self.store.reservas_ativas()
        self.assertTrue(reservas)
        self.assertTrue(all(l["ja_tem"] for l in self.store.carrinho_itens()))
        self.assertIn("Registrei o lote no histórico", texto)
        self.assertIn("despesas", texto)
        antes_preco = self.store.carrinho_resumo()
        self.assertGreater(antes_preco["despesas"], 0)
        self.assertEqual(antes_preco["saldo_gerencial"], round(80 - antes_preco["despesas"], 2))
        self.assertEqual(antes_preco["saldo"], 80)

        self.turno(f"Escolho vender o prato #{prato['id']} por R$ 8,50 por porção. Registre no cardápio.", texto, "registro")
        registro, provedor, _ = self.executar("registro")
        provedor.assert_not_called()
        self.assertIn("Registrei o prato", registro.choices[0].message.content)
        self.assertEqual(self.store.obter_prato(prato['id'])["preco_venda"], 8.50)
        self.assertEqual(self.store.reservas_ativas(), reservas)
        self.assertEqual(self.store.carrinho_resumo()["despesas"], antes_preco["despesas"])

    def test_resultado_nao_vaza_para_outro_turno_ou_sessao_e_so_finalizador_consume(self):
        montagem.iniciar_turno(SESSAO, "atual")
        montagem.registrar_resposta(SESSAO, ["Ficha conferida."], substituir=True, prioridade=20)
        resposta, provedor, _ = self.executar("atual")
        provedor.assert_not_called()
        self.assertEqual(montagem.resposta_preparada(SESSAO, "atual"), resposta.choices[0].message.content)
        for turno, sessao in (("outro", SESSAO), ("atual", "outra-sessao")):
            _, provedor, request = self.executar(turno, sessao=sessao)
            provedor.assert_called_once_with(request)
        self.assertIsNone(montagem.completar_resposta(resposta.choices[0].message.content, SESSAO, "atual"))
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "atual"))

    def test_aceite_com_ressalva_usa_impedimento_atual_sem_aceitar_perfil_antigo(self):
        self.turno("monte um prato equivalente com esses mesmos ingredientes",
                   "O Arroz de forno super-rápido ficou pendente. Quer uma adaptação?", "proposta")
        proposta, _, _ = self.executar("proposta")
        prato, = self.store.pratos()
        for sufixo in ("mas não tenho forno", "e troque o frango"):
            with self.subTest(ressalva=sufixo):
                self.store.perfil_salvar({"equipamentos": {"forno": True}, "restricoes": {"gas": "botijão"}})
                self.turno(f"Sim, aceito o prato #{prato['id']}, {sufixo}.",
                           proposta.choices[0].message.content, "ressalva")
                resposta, provedor, request = self.executar("ressalva")
                if "não tenho forno" in sufixo:
                    provedor.assert_not_called()
                    self.assertIn("forno", resposta.choices[0].message.content)
                    self.assertFalse(self.store.perfil()["equipamentos"]["forno"])
                else:
                    self.assertEqual(resposta, "resposta do provedor")
                    provedor.assert_called_once_with(request)
                self.assertEqual(self.store.analises_preco(), [])
                self.assertEqual(self.store.reservas_ativas(), [])
                self.assertNotEqual(self.store.obter_prato(prato["id"])["status"], "aceito")

    def test_formato_de_api_diferente_e_resposta_vazia_preservam_transporte(self):
        montagem.iniciar_turno(SESSAO, "formato")
        montagem.registrar_resposta(SESSAO, ["Ficha conferida."], prioridade=20)
        _, provedor, request = self.executar("formato", modo="anthropic_messages")
        provedor.assert_called_once_with(request)
        montagem.registrar_resposta(SESSAO, ["", "  "], prioridade=30)
        _, provedor, request = self.executar("formato")
        provedor.assert_called_once_with(request)
