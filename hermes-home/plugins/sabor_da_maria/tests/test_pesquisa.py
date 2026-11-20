"""A pesquisa de receitas (2.1) em ordem: o roteiro no painel e o próximo passo no resultado da busca."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sabor_da_maria import falas, jornada, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase

BUSCA_RECEITA = json.dumps({"success": True, "data": {"web": [{"title": "Frango com batata", "url": "https://a.com/x"}]}})
PAGINA = json.dumps({"results": [{"url": "https://a.com/x", "content": "# Frango\n## Ingredientes (schema.org/Recipe)\n- 1 frango\n"}]})


class TestQuandoPedeReceita(unittest.TestCase):
    def test_pedidos_que_viram_pesquisa(self):
        for fala in ("Oi, sou a Dona Maria. Quero montar o cardápio com o que tenho na despensa.",
                     "Gosto de fazer frango e carne moída, para marmita.",
                     "Pesquisa na internet 2 receitas de frango com batata",
                     "O que eu consigo fazer com o que tenho?",
                     "Me dá uma ideia de prato barato",
                     "Olha o perfil do meu negócio e os pratos mais vendidos na minha zona. Me sugere pratos.",
                     "Quero uma RECEITA de estrogonofe", "monte um prato para meu cardápio",
                     "crie uma receita", "prepare uma marmita", "monte um prato para meu cardápio?", "prepare uma marmita?"):
            self.assertTrue(pesquisa.pede_receita(fala), fala)

    def test_perguntas_que_a_despensa_responde_nao_viram_pesquisa(self):
        for fala in ("O que eu tenho na despensa e quanto paguei em cada coisa?",
                     "Mostra o cardápio e quanto ainda tenho dos R$ 80.",
                     "Aceito a marmita com frango; quero ver os preços.",
                     'Quero vender "Pizza de calabresa" no meu delivery. Abre os ingredientes com o valor de cada um.',
                     "Obrigada!", "", None, 42):
            self.assertFalse(pesquisa.pede_receita(fala), fala)

    def test_roteiro_segue_a_ordem_do_desafio_e_aguarda_escolha_antes_da_ficha(self):
        r = pesquisa.roteiro_do_turno("Quero uma receita")
        ordem = [r.index(p) for p in ("despensa_listar", "web_search", "web_extract", "despensa_buscar", "Qual opção você quer escolher?")]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIsNone(pesquisa.roteiro_do_turno("Obrigada!"))

    def test_pedido_especifico_prevalece_sobre_objetivo_e_objetivo_sobre_tipos(self):
        perfil = {"objetivo": "Não quero vender pizza. Quero começar vendendo marmitas.",
                  "alimentacao": ["caseira", "pizza", "marmita", "doces"]}
        geral = pesquisa.foco_da_busca(perfil, "Quero ideias de receitas para o meu negócio.")
        self.assertEqual((geral.assunto, geral.categorias), ("marmitas", ("marmita",)))
        self.assertTrue(pesquisa.pede_receita("Pesquise pizzas de frigideira."))
        especifico = pesquisa.foco_da_busca(perfil, "Pesquise pizzas de frigideira.")
        self.assertEqual(especifico.categorias, ("pizza",))
        livre = pesquisa.foco_da_busca({"objetivo": "Quero começar vendendo acarajé."}, "Quero ideias de receitas")
        self.assertEqual(livre.assunto, "acarajé")
        self.assertTrue(livre.combina("Acarajé tradicional"))
        self.assertFalse(livre.combina("Frango com batata"))
        estilos = pesquisa.foco_da_busca({"objetivo": "Quero aumentar as vendas.", "alimentacao": ["doces"]}, "Quero ideias")
        self.assertEqual(estilos.categorias, ("doces",))
        self.assertFalse(estilos.explicito)


class TestPainelComRoteiro(StoreTestCase):
    def setUp(self):
        super().setUp()
        store = Store()
        store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"], "estado": "SP", "cidade": "São Paulo"})
        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "ajuda_ia")

    def test_painel_leva_o_roteiro_so_quando_ela_pede_receita(self):
        com = painel.texto_do_painel(mensagem="Quero ideias de receita para marmita")
        sem = painel.texto_do_painel(mensagem="O que eu tenho na despensa e quanto paguei?")
        self.assertIn("PESQUISA DE RECEITAS", com)
        self.assertNotIn("PESQUISA DE RECEITAS", sem)
        self.assertNotIn("PESQUISA DE RECEITAS", painel.texto_do_painel())

    def test_hook_do_painel_recebe_a_mensagem_do_turno_e_marca_a_sessao(self):
        saida = painel.ao_iniciar_turno(is_first_turn=False, user_message="Pesquisa uma receita de frango", session_id="s1")
        self.assertIn("PESQUISA DE RECEITAS", saida["context"])
        self.assertTrue(pesquisa.pesquisando("s1"))
        self.assertNotIn("PESQUISA DE RECEITAS", painel.ao_iniciar_turno(user_message="Oi", session_id="s1")["context"])
        self.assertFalse(pesquisa.pesquisando("s1"))  # o turno seguinte não é de pesquisa: a marca sai

    def test_montagem_sem_globo_pesquisa_uma_fonte_direta_e_salva_rascunho(self):
        sessao = "montagem-com-web"
        self.addCleanup(pesquisa.esquecer_tudo)
        mensagem = "monte um prato para meu cardápio"
        falas.ao_iniciar_turno(session_id=sessao, user_message=mensagem)
        retorno = painel.ao_iniciar_turno(session_id=sessao, user_message=mensagem)
        self.assertIn("MONTAR UM PRATO PARA O CARDÁPIO", retorno["context"])
        self.assertTrue(pesquisa.montando(sessao))
        self.assertEqual(Store().pratos_em_andamento(), [])
        estoque = pesquisa.depois_da_ferramenta(tool_name="despensa_listar", result=tools.despensa_listar({}, session_id=sessao), session_id=sessao)
        self.assertIn("web_search AGORA", json.loads(estoque)["proximo_passo"])
        busca = pesquisa.depois_da_ferramenta(tool_name="web_search", result=BUSCA_RECEITA, session_id=sessao)
        self.assertIn("web_extract", json.loads(busca)["proximo_passo"])
        self.assertFalse(pesquisa.pediu_montar_um("Não monte um prato para meu cardápio."))

        dados = {"results": [{"url": "https://example.com/arroz", "title": "Arroz de forno",
                              "content": "# Arroz de forno\n## Ingredientes\n- 240 g de arroz\n## Modo de preparo\n1. Cozinhe o arroz."}]}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(dados), session_id=sessao)
        args = {"porcoes": 4, "tempo_preparo_min": 20, "ingredientes": [{"nome": "Arroz", "quantidade": 240, "unidade": "g"}]}
        resposta = json.loads(tools.receita_conferir_ingredientes(args, session_id=sessao))
        self.assertTrue(resposta["success"], resposta)
        self.assertEqual(resposta["receita"], "Arroz de forno")
        prato = Store().obter_prato(resposta["prato_id"])
        self.assertEqual(prato["nome"], "Arroz de forno")
        self.assertEqual(Store().obter_receita(resposta["receita_id"])["fonte_url"], "https://example.com/arroz")
        self.assertEqual(Store().reservas_ativas(), [])

    def test_coletanea_refina_busca_antes_de_recorrer_a_adaptacao_local(self):
        sessao = "montagem-com-lista"
        self.addCleanup(pesquisa.esquecer_tudo)
        pesquisa.marcar_turno(sessao, "monte um prato para meu cardápio")
        dados = {"results": [{"url": "https://example.com/materia", "title": "Almoço simples: 25 ideias de pratos",
                              "content": "# Receita qualquer\n## Ingredientes\n- arroz\n## Modo de preparo\n- cozinhe"}]}
        with patch("sabor_da_maria.montagem.propor_turno", return_value="Rascunho local gravado.") as montar:
            primeiro = pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(dados), session_id=sessao)
            self.assertIn("Refine agora", json.loads(primeiro)["proximo_passo"])
            montar.assert_not_called()
            self.assertTrue(pesquisa.montando(sessao))
            retorno = pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(dados), session_id=sessao)
        self.assertIn("Rascunho local gravado", json.loads(retorno)["proximo_passo"])
        self.assertIn("não é uma receita web verificada", json.loads(retorno)["proximo_passo"])
        self.assertIsNone(pesquisa.fonte_da_montagem(sessao))
        self.assertFalse(pesquisa.montando(sessao))
        self.assertTrue(montar.call_args.kwargs["permitir_adaptacao_local"])

    def test_extracao_com_wrapper_e_falha_parcial_continua_com_a_receita_util(self):
        sessao = "conducao-extracao-parcial"
        self.addCleanup(pesquisa.esquecer_tudo)
        pesquisa.marcar_turno(sessao, "monte um prato para meu cardápio", conduzida=True)
        pesquisa.depois_da_ferramenta(tool_name="despensa_listar", result='{"success":true}', session_id=sessao)
        pesquisa.depois_da_ferramenta(tool_name="web_search", result=BUSCA_RECEITA, session_id=sessao)
        pagina = {"url": "https://example.com/receita", "title": "Arroz com legumes",
                  "metadata": {"recipe_schema": True},
                  "content": "# Receita\nFonte: https://example.com/receita\n\n# Arroz com legumes\nFonte: https://example.com/receita\n"
                             "Rendimento: 4 | Tempo total: 30 min\n## Ingredientes (schema.org/Recipe)\n- 200 g de arroz\n"
                             "- 100 g de cenoura\n## Modo de preparo\n1. Cozinhe o arroz e a cenoura.\n\n## Texto da página (resumo)\nTexto auxiliar."}
        resultado = {"success": False, "error": "uma das páginas não respondeu",
                     "results": [{"url": "https://example.com/erro", "error": "timeout"}, pagina]}
        resposta = pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(resultado), session_id=sessao)
        self.assertEqual(pesquisa.ferramenta_da_conducao(sessao), "receita_conferir_ingredientes")
        self.assertEqual(pesquisa.fonte_da_montagem(sessao), ("Arroz com legumes", pagina["url"]))
        self.assertIn("receita_conferir_ingredientes", json.loads(resposta)["proximo_passo"])
        ficha = pesquisa.ficha_lida(sessao, pagina["url"])
        self.assertEqual(ficha["nome"], "Arroz com legumes")
        self.assertEqual(ficha["ingredientes"], ["200 g de arroz", "100 g de cenoura"])


class TestProximoPassoNoResultado(unittest.TestCase):
    def setUp(self):
        pesquisa.esquecer_tudo()
        self.addCleanup(pesquisa.esquecer_tudo)

    def _passo(self, ferramenta, args, resultado, sessao=""):
        saida = pesquisa.depois_da_ferramenta(tool_name=ferramenta, args=args, result=resultado, session_id=sessao)
        return None if saida is None else json.loads(saida)

    def test_busca_de_receita_manda_ler_a_pagina_e_conferir_a_despensa(self):
        r = self._passo("web_search", {"query": "receita fácil de frango com batata"}, BUSCA_RECEITA)
        self.assertIn("web_extract", r["proximo_passo"])
        self.assertIn("despensa_buscar", r["proximo_passo"])
        self.assertEqual(r["data"]["web"][0]["title"], "Frango com batata")  # o resultado original continua inteiro

    def test_leitura_da_receita_manda_conferir_a_despensa_e_perguntar(self):
        r = self._passo("web_extract", {"urls": ["https://a.com/x"]}, PAGINA)
        self.assertIn("despensa_buscar", r["proximo_passo"])
        self.assertIn("qual opção ela quer escolher", r["proximo_passo"])

    def test_no_turno_de_pesquisa_a_despensa_manda_buscar_e_depois_apresentar(self):
        pesquisa.marcar_turno("s1", "Quero montar o cardápio com o que tenho na despensa")
        lista = self._passo("despensa_listar", {}, '{"success": true, "itens": []}', "s1")
        self.assertIn("web_search AGORA", lista["proximo_passo"])
        conferida = self._passo("despensa_buscar", {}, '{"success": true, "resultados": []}', "s1")
        self.assertIn("sem usar o carrinho", conferida["proximo_passo"])
        self.assertIn("Qual opção você quer escolher?", conferida["proximo_passo"])
        # busca sem a palavra 'receita' também segue o roteiro, porque o turno é de pesquisa
        self.assertIn("web_extract", self._passo("web_search", {"query": "frango batata marmita"}, BUSCA_RECEITA, "s1")["proximo_passo"])

    def test_fora_do_turno_de_pesquisa_a_despensa_fica_como_esta(self):
        pesquisa.marcar_turno("s1", "O que eu tenho na despensa e quanto paguei?")
        self.assertIsNone(self._passo("despensa_listar", {}, '{"success": true}', "s1"))
        self.assertIsNone(self._passo("despensa_buscar", {}, '{"success": true}', "s1"))
        self.assertIsNone(self._passo("despensa_listar", {}, '{"success": true}', "outra-sessao"))
        self.assertIsNone(self._passo("despensa_listar", {}, '{"success": true}'))

    def test_busca_de_preco_e_pagina_sem_receita_ficam_como_estao(self):
        self.assertIsNone(self._passo("web_search", {"query": "preço creme de leite 200g"}, BUSCA_RECEITA))
        self.assertIsNone(self._passo("web_extract", {"urls": ["x"]}, json.dumps({"results": [{"content": "só texto"}]})))

    def test_busca_vazia_nao_pede_leitura_de_pagina_que_nao_existe(self):
        r = self._passo("web_search", {"query": "receita de frango com batata"}, '{"success": true, "data": {"web": []}}')
        self.assertIn("Tente outra consulta", r["proximo_passo"])
        self.assertNotIn("web_extract", r["proximo_passo"])

    def test_erro_lixo_e_outras_ferramentas_ficam_como_estao(self):
        pesquisa.marcar_turno("s1", "Quero uma receita")
        self.assertIsNone(self._passo("web_search", {"query": "receita"}, json.dumps({"success": False, "error": "sem rede"}), "s1"))
        self.assertIsNone(self._passo("web_search", {"query": "receita"}, "não é json", "s1"))
        self.assertIsNone(self._passo("web_search", {"query": "receita"}, ["lista"], "s1"))
        self.assertIsNone(self._passo("carrinho_consultar", {}, "{}", "s1"))
        self.assertIsNone(self._passo("web_search", None, BUSCA_RECEITA))

    def test_guarda_so_as_sessoes_mais_recentes(self):
        for i in range(pesquisa.MAX_SESSOES + 3):
            pesquisa.marcar_turno(f"s{i}", "Quero uma receita")
        self.assertFalse(pesquisa.pesquisando("s0"))
        self.assertTrue(pesquisa.pesquisando(f"s{pesquisa.MAX_SESSOES + 2}"))

    def test_conferencia_grande_preserva_a_pendencia_e_o_insumo_da_proxima_decisao(self):
        item = {"ingrediente": "Farinha", "quantidade_receita": "300 g", "item_despensa": "Farinha de trigo",
                "pendencia": "Confirme a medida", "como_resolver": "Informe em gramas", "conversao": "x" * 9000}
        bloqueio = {"tipo": "ingrediente_pendente", "detalhe": "Farinha: confirme a medida"}
        dados = {"success": True, "receita_id": 7, "ingredientes": [item], "bloqueios": [bloqueio],
                 "diga_a_dona_maria": "A ficha está salva.", "fonte_verificada": {"url": "https://exemplo.org/receita"}}
        compacta = self._passo("receita_conferir_ingredientes", {}, json.dumps(dados))
        self.assertEqual(compacta["bloqueios"], [bloqueio])
        self.assertEqual(compacta["ingredientes"], [{k: v for k, v in item.items() if k != "conversao"}])
        self.assertEqual(compacta["diga_a_dona_maria"], "A ficha está salva.")
        self.assertEqual(compacta["fonte_verificada"], dados["fonte_verificada"])


if __name__ == "__main__":
    unittest.main()
