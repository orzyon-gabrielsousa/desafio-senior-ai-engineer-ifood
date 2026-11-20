"""A escolha delega leitura e montagem, sem delegar decisões financeiras."""

import json
import unittest

from sabor_da_maria import escolhas, falas, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class ConducaoEscolhidoTests(unittest.TestCase):
    def setUp(self):
        pesquisa.esquecer_tudo()
        self.addCleanup(pesquisa.esquecer_tudo)
        self.prato = {"id": 7, "nome": "Frango da fonte", "status": "selecionado",
                      "receita_id": None, "fonte_url": "https://receitas.exemplo/receita/frango"}

    def test_escolha_inicia_na_fonte_e_nao_pede_pesquisa_ou_novo_aceite(self):
        self.assertTrue(pesquisa.conduzir_escolhido("s", self.prato))
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "web_extract")
        self.assertFalse(pesquisa.montando("s"))
        self.assertEqual(pesquisa.alvo_escolhido("s")["id"], 7)
        self.assertIsNone(pesquisa.alvo_escolhido("outro-chat"))
        resultado = {"results": [{"url": self.prato["fonte_url"], "title": "Frango da fonte",
            "metadata": {"recipe_schema": False, "recipe_textual": True},
            "receitas": [{"nome": "Frango da fonte", "ingredientes": ["500 g de frango"],
                          "modo_preparo": "Refogue o frango na panela.", "porcoes": 4}]}]}
        proximo = json.loads(pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(resultado), session_id="s"))
        self.assertIn("#7", proximo["proximo_passo"])
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "receita_conferir_ingredientes")
        self.assertIsNotNone(pesquisa.ficha_lida("s", self.prato["fonte_url"]))
        bloqueada = json.dumps({"success": True, "bloqueios": [{"tipo": "fonte_incompleta", "detalhe": "faltou arroz"}]})
        pesquisa.depois_da_ferramenta(tool_name="receita_conferir_ingredientes", result=bloqueada, session_id="s")
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "receita_conferir_ingredientes")
        pesquisa.depois_da_ferramenta(tool_name="receita_conferir_ingredientes", result=bloqueada, session_id="s")
        self.assertIsNone(pesquisa.ferramenta_da_conducao("s"))
        pesquisa.marcar_turno("s", None)
        self.assertFalse(pesquisa.pesquisando("s"))
        self.assertIsNone(pesquisa.alvo_escolhido("s"))

    def test_ficha_existente_ou_aprovada_nao_reabre_montagem_automaticamente(self):
        for prato in ({**self.prato, "receita_id": 3}, {**self.prato, "status": "aceito"}):
            self.assertFalse(pesquisa.conduzir_escolhido("s", prato))
            self.assertIsNone(pesquisa.ferramenta_da_conducao("s"))

    def pagina(self, url=None):
        return {"url": url or self.prato["fonte_url"], "title": "Frango da fonte",
                "metadata": {"recipe_schema": True}, "receitas": [{"nome": "Frango da fonte", "porcoes": 4,
                    "ingredientes": ["500 g de frango"], "modo_preparo": "Refogue o frango na panela."}]}

    def test_escolha_usa_contrato_verificado_no_turno_anterior_sem_refazer_leitura(self):
        pesquisa.marcar_turno("s", "Quero opções de receitas", somente_sugestoes=True)
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [self.pagina()]}), session_id="s")
        pesquisa.marcar_turno("s", None)
        self.assertTrue(pesquisa.conduzir_escolhido("s", self.prato))
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "receita_conferir_ingredientes")
        self.assertEqual(pesquisa.ficha_lida("s", self.prato["fonte_url"])["ingredientes"], ["500 g de frango"])
        self.assertFalse(pesquisa.montando("s"))
        self.assertTrue(pesquisa.conduzir_escolhido("outra-sessao", self.prato))
        self.assertEqual(pesquisa.ferramenta_da_conducao("outra-sessao"), "web_extract")

    def test_url_escolhida_nao_e_substituida_e_leitura_para_apos_uma_retentativa(self):
        montagem.iniciar_turno("s", "leitura-limitada")
        pesquisa.conduzir_escolhido("s", self.prato)
        outra = {"results": [self.pagina("https://outra.exemplo/receita/diferente")]}
        primeira = json.loads(pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps(outra), session_id="s"))
        self.assertEqual(primeira["results"], [])
        self.assertIn(self.prato["fonte_url"], primeira["proximo_passo"])
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "web_extract")
        self.assertIsNone(pesquisa.ficha_lida("s", "https://outra.exemplo/receita/diferente"))
        segunda = json.loads(pesquisa.depois_da_ferramenta(tool_name="web_extract", result="falha de leitura", session_id="s"))
        self.assertIsNone(pesquisa.ferramenta_da_conducao("s"))
        self.assertIn("Não faça outra busca ou leitura", segunda["proximo_passo"])
        resposta = montagem.resposta_preparada("s", "leitura-limitada")
        self.assertIn("Não consegui ler a receita completa", resposta)
        self.assertEqual(resposta.count("?"), 1)
        self.assertEqual(pesquisa.alvo_escolhido("s")["fonte_url"], self.prato["fonte_url"])

    def test_sugestoes_removem_pagina_sem_contrato_e_cacheia_so_receita_direta(self):
        pesquisa.marcar_turno("s", "Quero opções de receitas", somente_sugestoes=True, conduzida=True)
        descartada = {"url": "https://globo.exemplo/sem-ficha", "title": "Frango da Globo", "metadata": {"recipe_schema": False},
                      "receitas": [{"nome": "Frango da Globo", "ingredientes": ["500 g de frango"], "modo_preparo": "Refogue."}]}
        resultado = json.loads(pesquisa.depois_da_ferramenta(tool_name="web_extract",
                                result=json.dumps({"results": [descartada, self.pagina()]}), session_id="s"))
        self.assertEqual(resultado["results"], [self.pagina()])
        self.assertNotIn("Frango da Globo", resultado["proximo_passo"])
        self.assertIn("Somente estas receitas", resultado["proximo_passo"])
        self.assertIsNone(pesquisa.ficha_lida("s", descartada["url"]))
        self.assertIsNotNone(pesquisa.ficha_lida("s", self.prato["fonte_url"]))

    def test_sugestoes_sem_receita_direta_encerram_apos_duas_leituras_inuteis(self):
        montagem.iniciar_turno("s", "sugestoes-limitadas")
        pesquisa.marcar_turno("s", "Quero opções de receitas", somente_sugestoes=True, conduzida=True)
        sem_ficha = json.dumps({"results": [{"url": "https://exemplo/lista", "title": "Ideias de almoço", "content": "Veja receitas"}]})
        primeira = json.loads(pesquisa.depois_da_ferramenta(tool_name="web_extract", result=sem_ficha, session_id="s"))
        self.assertEqual(primeira["results"], [])
        self.assertEqual(pesquisa.ferramenta_da_conducao("s"), "web_search")
        self.assertIsNone(montagem.resposta_preparada("s", "sugestoes-limitadas"))
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=sem_ficha, session_id="s")
        self.assertIsNone(pesquisa.ferramenta_da_conducao("s"))
        self.assertTrue(pesquisa.somente_sugestoes("s"))
        self.assertIn("Não consegui ler uma receita real completa", montagem.resposta_preparada("s", "sugestoes-limitadas"))

    def test_lista_final_so_contem_fontes_verificadas_deste_turno_e_custo_exato(self):
        pesquisa.marcar_turno("s", "Quero opções de receitas", somente_sugestoes=True)
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [self.pagina("https://exemplo/receita-antiga")]}), session_id="s")
        montagem.iniciar_turno("s", "opcoes-verificadas")
        pesquisa.marcar_turno("s", "Quero opções de receitas", somente_sugestoes=True)
        pagina = self.pagina()
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [pagina,
            {"url": "https://globo.exemplo/invalida", "title": "Segunda opção inventada", "content": "Resumo sem receita completa"}]}), session_id="s")
        resultado = {"success": True, "resultados": [
            {"procurado": "frango", "encontrado": True, "candidatos": [{"nome": "frango", "confiavel": True, "custo_unitario_legivel": "R$ 14,00/kg"}]},
            {"procurado": "coxinha da asa", "encontrado": True, "candidatos": [{"nome": "frango", "confiavel": True, "custo_unitario_legivel": "R$ 999,00/kg"}]}]}
        pesquisa.depois_da_ferramenta(tool_name="despensa_buscar", result=json.dumps(resultado), session_id="s")
        resposta = montagem.resposta_preparada("s", "opcoes-verificadas")
        self.assertEqual(resposta.count("?"), 1)
        self.assertIn("500 g de frango", resposta)
        self.assertIn("R$ 14,00/kg", resposta)
        for proibido in ("globo.exemplo", "inventada", "receita-antiga", "R$ 999,00", "Já tem", "<html"):
            self.assertNotIn(proibido, resposta)
        self.assertEqual(escolhas.opcoes_exibidas(resposta), [{"numero": 1, "nome": "Frango da fonte", "fonte_url": self.prato["fonte_url"]}])
        self.assertEqual(escolhas.escolhidas("Escolho a opção 1.", resposta)[0]["fonte_url"], self.prato["fonte_url"])


class RetomadaSemHistoricoTests(StoreTestCase):
    def test_ficha_escolhida_adota_a_fonte_lida_sem_retranscrever_ingredientes(self):
        store = Store()
        store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "ajuda_ia")
        store.perfil_salvar({"equipamentos": {"fogao": 4}, "tecnicas": {"refogar": "sim"},
                             "restricoes": {"gas": "botijão", "energia": "estável", "espaco_geladeira": "bastante",
                                            "tempo_por_cozinhada_min": 90}})
        url = "https://receitas.exemplo/receita/frango"
        prato, = store.selecionar_pratos([{"nome": "Frango da fonte", "fonte_url": url}])
        sessao = "retomada-sem-lista-anterior"
        self.addCleanup(pesquisa.esquecer_tudo)
        self.assertTrue(pesquisa.conduzir_escolhido(sessao, prato))
        pagina = {"url": url, "title": prato["nome"], "metadata": {"recipe_schema": True}, "receitas": [{
            "nome": prato["nome"], "porcoes": None, "ingredientes": ["500 g de peito de frango"],
            "modo_preparo": "Refogue o frango na panela.", "tempo_preparo_min": 35}]}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [pagina]}), session_id=sessao)
        falas.registrar(sessao, [], f"Quero continuar o prato escolhido #{prato['id']}.")
        resultado = json.loads(tools.receita_conferir_ingredientes({}, session_id=sessao))
        self.assertTrue(resultado["success"], resultado)
        ficha = store.obter_receita(resultado["receita_id"])
        self.assertEqual(ficha["dados"]["fonte_verificada"]["url"], url)
        self.assertEqual(ficha["dados"]["ingredientes"][0]["quantidade"], 500)
        self.assertEqual(jornada.resumo(store)["pergunta"]["texto"], jornada.PERGUNTA_RENDIMENTO)
        painel.ao_iniciar_turno(session_id=sessao, turn_id="rendimento", user_message="4",
                             conversation_history=[{"role": "assistant", "content": jornada.PERGUNTA_RENDIMENTO}])
        self.assertEqual(jornada.resumo(store)["etapa"], "aceite")
        salva = store.obter_receita(ficha["id"])
        self.assertEqual(salva["porcoes"], 4)
        self.assertIsNone(salva["dados"]["fonte_verificada"]["porcoes"])
        self.assertEqual(store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertFalse(store.reservas_ativas())
