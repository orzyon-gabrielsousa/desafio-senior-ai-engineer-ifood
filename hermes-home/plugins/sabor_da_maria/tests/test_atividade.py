"""O passo a passo da consultora: uma frase por ferramenta, guardada por chamada e entregue à tela."""

from __future__ import annotations

import json
import os
import unittest

from sabor_da_maria import api, atividade, store as store_mod, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase

BUSCA = {"success": True, "data": {"web": [
    {"title": "Marmita da Semana: Frango com Batata Fácil - Receitas de Minuto", "url": "https://receitasdeminuto.com/marmita/", "position": 1},
    {"title": "Frango com Batatas na Marmita", "url": "https://www.tuta.com.br/receita/frango/", "position": 2},
    {"title": "Receitas de frango com batata", "url": "https://www.receiteria.com.br/receitas/", "position": 3},
    {"title": "Outro", "url": "https://www.panelinha.com.br/x", "position": 4},
]}}

PAGINA = {"results": [{
    "url": "https://receitasdeminuto.com/marmita/", "title": "Marmita da Semana - Receitas de Minuto",
    "content": ("# Frango com Batata Fácil\nFonte: https://receitasdeminuto.com/marmita/\nRendimento: 4, 4 porções | Categoria: Principal\n"
                "## Ingredientes (schema.org/Recipe)\n- 12 coxinhas da asa de Frango\n- 4 batatas médias\n- 1/2 cebola (picada)\n"
                "## Modo de preparo\n1. Refogue a cebola.\n"),
}]}


class TestFrasePorFerramenta(unittest.TestCase):
    def test_ficha_parcial_sem_cmv_continua_aparecendo_na_atividade(self):
        resultado = {"success": True, "receita": "Frango", "veredito": "precisa_confirmar",
                     "resumo": {"cmv_previa": None, "compras_total_estimado": 0}, "ingredientes": []}
        descricao = atividade.descrever("receita_conferir_ingredientes", {}, json.dumps(resultado))
        self.assertIn("precisa confirmar", descricao["resumo"])
        self.assertNotIn("custo da receita", descricao["resumo"])

    def test_busca_na_internet_diz_quantos_resultados_e_de_onde(self):
        d = atividade.descrever("web_search", {"query": "receita de frango com batata"}, json.dumps(BUSCA))
        self.assertEqual(d["titulo"], "Pesquisou na internet")
        self.assertTrue(d["ok"])
        self.assertIn("4 resultados", d["resumo"])
        self.assertIn("receitasdeminuto.com", d["resumo"])
        self.assertIn("tuta.com.br", d["resumo"])  # sem o www.
        self.assertIn("e mais 1", d["resumo"])
        self.assertEqual(d["entrada"], "receita de frango com batata")
        self.assertIn("Frango com Batatas na Marmita — tuta.com.br", d["itens"])

    def test_busca_sem_resultado(self):
        d = atividade.descrever("web_search", {"query": "xyz"}, {"success": True, "data": {"web": []}})
        self.assertIn("Nenhum resultado", d["resumo"])

    def test_leitura_da_receita_conta_os_ingredientes(self):
        d = atividade.descrever("web_extract", {"urls": ["https://receitasdeminuto.com/marmita/"]}, json.dumps(PAGINA))
        self.assertEqual(d["titulo"], "Leu a receita")
        self.assertIn("Frango com Batata Fácil (3 ingredientes, rende 4, 4 porções)", d["resumo"])
        self.assertEqual(d["entrada"], "receitasdeminuto.com")
        self.assertIn("4 batatas médias", d["itens"][0])

    def test_leitura_de_duas_paginas_e_pagina_sem_receita(self):
        sem_receita = {"url": "https://exemplo.com.br/blog", "title": "Blog", "content": "só texto da página"}
        d = atividade.descrever("web_extract", {"urls": ["a", "b"]}, {"results": [PAGINA["results"][0], sem_receita]})
        self.assertEqual(d["titulo"], "Leu 2 páginas")
        self.assertIn("sem lista de ingredientes estruturada", d["resumo"])

    def test_leitura_que_falhou_aparece_como_falha(self):
        d = atividade.descrever("web_extract", {"urls": ["x"]}, {"success": False, "error": "timeout ao ler a página"})
        self.assertFalse(d["ok"])
        self.assertIn("timeout", d["resumo"])

    def test_leitura_parcial_nao_conta_url_que_falhou(self):
        resultado = {"results": [PAGINA["results"][0], {"url": "https://exemplo.com.br/receita", "error": "timeout"}]}
        d = atividade.descrever("web_extract", {}, resultado)
        self.assertEqual(d["titulo"], "Leu 1 página")
        self.assertIn("exemplo.com.br: não consegui ler (timeout)", d["itens"])

    def test_despensa_separa_o_que_tem_do_que_precisa_comprar(self):
        resultado = {"success": True, "resultados": [
            {"procurado": "frango", "encontrado": True, "candidatos": [
                {"nome": "Peito de frango", "disponivel_legivel": "2 kg", "custo_unitario_legivel": "R$ 14,00/kg"}]},
            {"procurado": "creme de leite", "encontrado": False, "candidatos": []}]}
        d = atividade.descrever("despensa_buscar", {"ingredientes": ["frango", "creme de leite"]}, resultado)
        self.assertEqual(d["resumo"], "Tem: frango (2 kg) · Não tem: creme de leite")
        self.assertEqual(d["entrada"], "frango, creme de leite")
        self.assertIn("frango → Peito de frango: 2 kg disponível, R$ 14,00/kg", d["itens"])
        self.assertIn("creme de leite → não tem na despensa (precisa comprar)", d["itens"])

    def test_erro_da_ferramenta_vira_passo_com_falha(self):
        d = atividade.descrever("prato_calcular_cmv", {}, {"success": False, "error": "KeyError: receita_id 7 não encontrada"})
        self.assertFalse(d["ok"])
        self.assertIn("receita_id 7", d["resumo"])
        self.assertEqual(d["titulo"], "Calculou o CMV")

    def test_trava_de_aceite_nao_e_falha_e_mostra_o_motivo(self):
        resultado = {"success": False, "bloqueado": True, "error": "falta a cozinha dela",
                     "bloqueios": [{"tipo": "perfil_pendente", "detalhe": "Seu fogão tem quantas bocas?"}, {"tipo": "x", "detalhe": "Tem forno?"}]}
        d = atividade.descrever("cardapio_registrar", {"prato_id": 1}, resultado)
        self.assertTrue(d["ok"])
        self.assertTrue(d["resumo"].startswith(atividade.PREFIXO_TRAVA))
        self.assertIn("falta a cozinha dela", d["resumo"])
        self.assertEqual(d["itens"], ["Seu fogão tem quantas bocas?", "Tem forno?"])

    def test_ferramenta_desconhecida_e_lixo_nao_derrubam(self):
        self.assertEqual(atividade.descrever("ferramenta_nova", {}, "não é json")["titulo"], "Ferramenta nova")
        for lixo in (None, [], 42, "[]", '{"success": true}'):
            d = atividade.descrever("despensa_buscar", None, lixo)
            self.assertIn("titulo", d)
            self.assertIn("resumo", d)

    def test_texto_longo_e_cortado(self):
        d = atividade.descrever("web_search", {"query": "q" * 500}, {"success": True, "data": {"web": [
            {"title": "t" * 400, "url": "https://a.com/x"}]}})
        self.assertLessEqual(len(d["entrada"]), 140)
        self.assertLessEqual(len(d["itens"][0]), atividade.MAX_ITEM)


class TestFrasesComAsToolsDeVerdade(StoreTestCase):
    """Os formatos de resultado vêm das tools de verdade: se uma delas mudar de forma, o teste avisa."""

    def _rodar(self, nome: str, args: dict) -> dict:
        return atividade.descrever(nome, args, tools.HANDLERS[nome](args))

    def test_despensa_listar(self):
        d = self._rodar("despensa_listar", {})
        self.assertRegex(d["resumo"], r"^\d+ itens na despensa")
        self.assertIn("R$ 80,00", d["resumo"])
        self.assertTrue(d["itens"][0].count(" a R$ ") == 1, d["itens"][0])  # ingrediente, quanto tem e o preço por quilo

    def test_despensa_listar_com_filtro_diz_o_que_procurou(self):
        d = self._rodar("despensa_listar", {"filtro": "frango"})
        self.assertRegex(d["resumo"], r"^1 item com “frango”")
        self.assertEqual(d["entrada"], "frango")

    def test_filtro_com_varios_termos_traz_os_que_casam_com_qualquer_um(self):
        d = self._rodar("despensa_listar", {"filtro": "frango, batata"})
        self.assertRegex(d["resumo"], r"^[2-9] itens com “frango, batata”")
        achados = " ".join(d["itens"])
        self.assertIn("Peito de frango", achados)
        self.assertIn("Batata", achados)

    def test_despensa_buscar_com_a_planilha_real(self):
        d = self._rodar("despensa_buscar", {"ingredientes": ["frango", "xyzwq"]})
        self.assertTrue(d["resumo"].startswith("Tem: frango"), d["resumo"])
        self.assertIn("Não tem: xyzwq", d["resumo"])

    def test_conferencia_mostra_a_conta_de_cada_ingrediente(self):
        d = self._rodar("receita_conferir_ingredientes", {"prato": "Frango com batata", "ingredientes": [
            {"nome": "peito de frango", "quantidade": 600, "unidade": "g"}]})
        self.assertEqual(d["titulo"], "Comparou com a despensa e o orçamento")
        self.assertIn("tem 1", d["resumo"])
        self.assertIn("cabe no orçamento", d["resumo"])
        self.assertIn("peito de frango: 600 g × R$ 14,00/kg = R$ 8,40", d["itens"])

    def test_carrinho_traz_saldo_e_o_texto_pronto(self):
        d = self._rodar("carrinho_adicionar", {"nome": "couve", "preco_total": 3})
        self.assertEqual(d["titulo"], "Colocou no carrinho")
        self.assertIn("saldo R$ 77,00", d["resumo"])

    def test_trava_do_perfil_incompleto_aparece_como_travado(self):
        d = self._rodar("cardapio_registrar", {})  # sem prato_id: a tool recusa
        self.assertFalse(d["ok"])

    def test_negocio_nao_guardado_diz_que_ela_nao_falou(self):
        d = self._rodar("negocio_salvar", {"cidade": "Campinas"})  # ela não disse nada nesta sessão
        self.assertTrue(d["ok"])
        self.assertTrue(d["resumo"].startswith(atividade.PREFIXO_TRAVA))

    def test_analytics_vazio_nao_injeta_pratos(self):
        d = self._rodar("analytics_analises_consultar", {})
        self.assertEqual(d["titulo"], "Consultou as análises salvas")
        self.assertEqual(d["itens"], [])


class TestRegistroPorChamada(StoreTestCase):
    def test_hook_guarda_e_a_tela_busca_pelo_id_da_chamada(self):
        atividade.apos_tool(tool_name="web_search", args={"query": "receita de frango"}, result=json.dumps(BUSCA), session_id="s1",
                            tool_call_id="call-1", duration_ms=830)
        atividade.apos_tool(tool_name="despensa_listar", args={}, result=tools.HANDLERS["despensa_listar"]({}), session_id="s1", tool_call_id="call-2")
        achado = Store().atividade_das_chamadas(["call-1", "call-2", "nao-existe"])
        self.assertEqual(set(achado), {"call-1", "call-2"})
        self.assertEqual(achado["call-1"]["titulo"], "Pesquisou na internet")
        self.assertEqual(achado["call-1"]["entrada"], "receita de frango")
        self.assertEqual(achado["call-1"]["duracao_ms"], 830)
        self.assertTrue(achado["call-1"]["ok"])

    def test_repetir_a_chamada_atualiza_em_vez_de_duplicar(self):
        for resultado in ({"success": True, "data": {"web": []}}, BUSCA):
            atividade.apos_tool(tool_name="web_search", args={"query": "q"}, result=resultado, tool_call_id="call-1")
        self.assertEqual(len(Store().atividade_recente()), 1)
        self.assertIn("4 resultados", Store().atividade_das_chamadas(["call-1"])["call-1"]["resumo"])

    def test_sem_id_da_chamada_ou_ferramenta_interna_nao_guarda(self):
        atividade.apos_tool(tool_name="web_search", args={}, result=BUSCA, tool_call_id="")
        atividade.apos_tool(tool_name="_interna", args={}, result={}, tool_call_id="call-9")
        self.assertEqual(Store().atividade_recente(), [])

    def test_guarda_so_as_mais_recentes(self):
        antes = store_mod.MAX_ATIVIDADE
        store_mod.MAX_ATIVIDADE = 3
        try:
            for i in range(6):
                atividade.apos_tool(tool_name="despensa_listar", args={}, result={"total_itens": i}, tool_call_id=f"c{i}")
        finally:
            store_mod.MAX_ATIVIDADE = antes
        self.assertEqual([a["chamada"] for a in Store().atividade_recente()], ["c5", "c4", "c3"])

    def test_banco_com_problema_nao_derruba_o_turno(self):
        os.environ["SABOR_DA_MARIA_DB"] = self._tmp.name  # um diretório, não um arquivo
        with self.assertLogs("sabor_da_maria.atividade", level="ERROR"):  # fica no log, o turno segue
            atividade.apos_tool(tool_name="web_search", args={}, result=BUSCA, tool_call_id="call-1")

    def test_api_entrega_por_ids_e_as_recentes(self):
        atividade.apos_tool(tool_name="web_search", args={"query": "q"}, result=BUSCA, tool_call_id="call-1")
        atividade.apos_tool(tool_name="despensa_listar", args={}, result={"total_itens": 3}, tool_call_id="call-2")
        status, corpo = api.tratar("GET", "/api/atividade?ids=call-1,nada")
        self.assertEqual(status, 200)
        self.assertEqual(set(corpo["atividade"]), {"call-1"})
        status, corpo = api.tratar("GET", "/api/atividade?limite=1")
        self.assertEqual([a["chamada"] for a in corpo["recentes"]], ["call-2"])
        self.assertEqual(api.tratar("POST", "/api/atividade", {})[0], 405)


if __name__ == "__main__":
    unittest.main()
