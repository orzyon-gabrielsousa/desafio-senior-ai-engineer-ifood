"""Ajuda oferece opções; montar uma ficha exige escolha ou delegação direta."""

import copy
import json
from unittest.mock import Mock

from sabor_da_maria import encerramento, falas, jornada, montagem, painel, pesquisa
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


PEDIDO = ("Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. "
          "Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.")
LEGADO = ("Quero sua ajuda. Pesquise uma receita real para o meu negócio com o estoque disponível "
          "e monte uma proposta, sem aprovar por mim.")
URL = "https://receitas.exemplo/frango-refogado/"
LISTA = (f"### 1. Frango refogado\nFonte: [Frango refogado]({URL})\nJá tem: peito de frango e cebola.\n"
         "Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.")


class ConsentimentoDasSugestoesTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo"})

    def hook(self, mensagem, turno="sugestoes", anterior=""):
        historico = [{"role": "assistant", "content": anterior}] if anterior else []
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=mensagem)
        return painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, conversation_history=historico, user_message=mensagem)

    def test_confirmar_estoque_para_na_escolha_de_ajuda_sem_pesquisa(self):
        self.hook("Sim, estou pronta para começar a montar meus pratos.")
        estado = jornada.resumo(self.store)
        self.assertEqual(estado["etapa"], "ajuda")
        self.assertFalse(pesquisa.pesquisando(SESSAO))
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(self.store.pratos(), [])
        self.assertEqual(next(o for o in estado["pergunta"]["opcoes"] if o["acao"] == "ajuda_ia")["mensagem"], PEDIDO)

    def test_cta_novo_e_legado_pesquisam_opcoes_e_param_antes_de_montar(self):
        jornada.responder(self.store, "confirmar_estoque")
        for mensagem in (PEDIDO, LEGADO):
            retorno = self.hook(mensagem)
            self.assertTrue(pesquisa.somente_sugestoes(SESSAO), mensagem)
            self.assertFalse(pesquisa.montando(SESSAO))
            self.assertIn("PESQUISA DE RECEITAS", retorno["context"])
            self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", retorno["context"])
            etapas = [("despensa_listar", {"success": True}),
                      ("web_search", {"success": True, "data": {"web": [{"url": URL}]}}),
                      ("web_extract", {"results": [{"url": URL, "title": "Frango refogado", "metadata": {"recipe_schema": True},
                          "receitas": [{"nome": "Frango refogado", "url": URL, "porcoes": 4,
                                       "ingredientes": ["500 g de peito de frango", "1 cebola"],
                                       "modo_preparo": "Refogue o frango e a cebola em uma panela."}]}]}),
                      ("despensa_buscar", {"success": True})]
            for ferramenta, dados in etapas:
                self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), ferramenta)
                retorno = pesquisa.depois_da_ferramenta(tool_name=ferramenta, result=json.dumps(dados), session_id=SESSAO)
            self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
            self.assertTrue(pesquisa.somente_sugestoes(SESSAO))
            self.assertIn("Qual opção você quer escolher?", json.loads(retorno)["proximo_passo"])
            self.assertEqual(self.store.pratos(), [])
            self.assertEqual(self.store.receitas(), [])
            self.assertEqual(self.store.reservas_ativas(), [])
            self.assertEqual(self.store.analises_preco(), [])
            self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_post_de_prontidao_seguido_da_mensagem_do_botao_aguarda_escolha(self):
        jornada.responder(self.store, "confirmar_estoque")
        self.hook("Sim, estou pronta para começar a montar meus pratos.", "prontidao-post")
        pronta = montagem.resposta_preparada(SESSAO, "prontidao-post")
        self.assertIsNotNone(pronta)
        self.assertIn("Quer ajuda para pesquisar um prato ou prefere montar o seu?", pronta)
        self.assertEqual(pronta.count("?"), 1)
        self.assertFalse(pesquisa.pesquisando(SESSAO))
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(self.store.pratos(), [])

    def test_modelo_de_sugestoes_so_recebe_ferramentas_de_leitura(self):
        jornada.responder(self.store, "confirmar_estoque")
        self.hook(PEDIDO)
        nomes = ["despensa_listar", "web_search", "web_extract", "despensa_buscar", "perfil_cozinha_consultar",
                 "pratos_selecionar", "receita_conferir_ingredientes", "prato_calcular_cmv", "perfil_cozinha_salvar",
                 "despensa_registrar_compras", "prato_registrar", "terminal", "nova_ferramenta_de_escrita"]
        request = {"tools": [{"type": "function", "function": {"name": nome}} for nome in nomes]}
        antes = copy.deepcopy(request)
        provedor = Mock(return_value="sugestoes")
        encerramento.executar(request, provedor, api_mode="chat_completions", session_id=SESSAO, turn_id="sugestoes")
        enviado = provedor.call_args.args[0]
        self.assertEqual([t["function"]["name"] for t in enviado["tools"]], nomes[:5])
        self.assertEqual(enviado["tool_choice"]["function"]["name"], "despensa_listar")
        self.assertEqual(request, antes)

    def test_escolha_posterior_libera_montagem_da_ficha_escolhida(self):
        jornada.responder(self.store, "confirmar_estoque")
        self.hook(PEDIDO)
        self.hook("Escolho o prato 1.", "escolha", LISTA)
        self.assertFalse(pesquisa.somente_sugestoes(SESSAO))
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_extract")
        self.assertEqual([(p["nome"], p["status"]) for p in self.store.pratos()], [("Frango refogado", "selecionado")])
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_pedido_direto_de_montagem_continua_delegacao_deliberada(self):
        jornada.responder(self.store, "confirmar_estoque")
        retorno = self.hook("Monte um prato para meu cardápio.")
        self.assertFalse(pesquisa.somente_sugestoes(SESSAO))
        self.assertTrue(pesquisa.montando(SESSAO))
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")
        self.assertIn("MONTAR UM PRATO PARA O CARDÁPIO", retorno["context"])
