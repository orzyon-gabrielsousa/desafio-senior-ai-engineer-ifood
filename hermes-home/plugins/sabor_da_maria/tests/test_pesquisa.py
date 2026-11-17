"""A pesquisa de receitas (2.1) em ordem: o roteiro no painel e o próximo passo no resultado da busca."""

from __future__ import annotations

import json
import unittest

from sabor_da_maria import painel, pesquisa

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
                     "Quero uma RECEITA de estrogonofe"):
            self.assertTrue(pesquisa.pede_receita(fala), fala)

    def test_perguntas_que_a_despensa_responde_nao_viram_pesquisa(self):
        for fala in ("O que eu tenho na despensa e quanto paguei em cada coisa?",
                     "Mostra o cardápio e quanto ainda tenho dos R$ 80.",
                     "Aceito a marmita com frango; quero ver os preços.",
                     'Quero vender "Pizza de calabresa" no meu delivery. Abre os ingredientes com o valor de cada um.',
                     "Obrigada!", "", None, 42):
            self.assertFalse(pesquisa.pede_receita(fala), fala)

    def test_roteiro_segue_a_ordem_do_desafio_e_termina_na_pergunta_de_feedback(self):
        r = pesquisa.roteiro_do_turno("Quero uma receita")
        ordem = [r.index(p) for p in ("despensa_listar", "web_search", "web_extract", "despensa_buscar", "Você gosta de cozinhar isso? Vê algum impedimento?")]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIsNone(pesquisa.roteiro_do_turno("Obrigada!"))


class TestPainelComRoteiro(StoreTestCase):
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
        self.assertIn("Você gosta de cozinhar isso?", r["proximo_passo"])

    def test_no_turno_de_pesquisa_a_despensa_manda_buscar_e_depois_apresentar(self):
        pesquisa.marcar_turno("s1", "Quero montar o cardápio com o que tenho na despensa")
        lista = self._passo("despensa_listar", {}, '{"success": true, "itens": []}', "s1")
        self.assertIn("web_search AGORA", lista["proximo_passo"])
        conferida = self._passo("despensa_buscar", {}, '{"success": true, "resultados": []}', "s1")
        self.assertIn("sem usar o carrinho", conferida["proximo_passo"])
        self.assertIn("Você gosta de cozinhar isso?", conferida["proximo_passo"])
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


if __name__ == "__main__":
    unittest.main()
