"""A proposta é persistida antes de pedir fatos da cozinha, sem inventá-los."""

import json

from sabor_da_maria import falas, fonte, montagem, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase, diz


class RascunhoSemCozinhaTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()

    def conferir(self, **extras):
        dados = {"nome": "Frango da casa", "porcoes": 4, "tempo_preparo_min": 45,
                 "equipamentos_necessarios": ["fogão"], "tecnicas_necessarias": ["refogar"],
                 "ingredientes": [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}],
                 "confirmacoes_operacionais": {"gas": "Sim", "energia": "Sim", "espaco_geladeira": "Sim"}}
        return json.loads(tools.receita_conferir_ingredientes({**dados, **extras}, session_id=SESSAO))

    def test_cozinha_vazia_e_confirmacoes_inventadas_preservam_rascunho_pendente(self):
        diz("Monte um prato para meu cardápio.")
        montagem.iniciar_turno(SESSAO, "cozinha-vazia")
        perfil, ingredientes, orcamento = self.store.perfil(), self.store.ingredientes(), self.store.orcamento()
        ficha = self.conferir()
        self.assertTrue(ficha["success"], ficha)
        self.assertTrue(ficha["rascunho_salvo"])
        self.assertNotIn("error", ficha)
        pronta = montagem.resposta_preparada(SESSAO, "cozinha-vazia")
        self.assertIsNotNone(pronta)
        self.assertEqual(pronta.count("?"), 1)
        self.assertIn(f"#{ficha['prato_id']}", pronta)
        self.assertNotIn("ValueError", pronta)
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual({c["chave"] for c in ficha["confirmacoes_recusadas"]}, {"gas", "energia", "espaco_geladeira"})
        prato = self.store.obter_prato(ficha["prato_id"])
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual(prato["status"], "selecionado")
        self.assertEqual(receita["dados"]["confirmacoes_operacionais"], [])
        self.assertTrue({"gas_a_confirmar", "energia_a_confirmar", "refrigeracao_a_confirmar"} <= {b["tipo"] for b in ficha["bloqueios"]})
        cmv = json.loads(tools.prato_calcular_cmv({"prato_id": prato["id"]}))
        self.assertFalse(cmv["success"], cmv)
        registro = json.loads(tools.cardapio_registrar({"prato_id": prato["id"], "preco_escolhido": 10}, session_id=SESSAO))
        self.assertFalse(registro["success"], registro)
        self.assertEqual((self.store.perfil(), self.store.ingredientes(), self.store.orcamento()), (perfil, ingredientes, orcamento))
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])

    def test_quiabo_nao_vira_frango_por_mapeamento_explicito_do_modelo(self):
        diz("Monte um prato para meu cardápio.")
        antes = self.store.ingredientes()
        ficha = self.conferir(ingredientes=[{"nome": "quiabo", "item_despensa": "Peito de frango", "quantidade": 1, "unidade": "kg"},
                                         {"nome": "frango", "item_despensa": "Peito de frango", "quantidade": 500, "unidade": "g"}])
        self.assertTrue(ficha["rascunho_salvo"], ficha)
        self.assertTrue(any("quiabo" in p and "não corresponde" in p for p in ficha["pendencias"]))
        self.assertIsNone(ficha["resumo"]["cmv_previa"])
        quiabo = next(i for i in ficha["ingredientes"] if i["ingrediente"] == "quiabo")
        self.assertNotIn("item_despensa", quiabo)
        self.assertEqual(self.store.ingredientes(), antes)
        self.assertFalse(json.loads(tools.prato_calcular_cmv({"prato_id": ficha["prato_id"]}))["success"])

    def test_sal_e_pimenta_nao_sao_custeados_como_um_unico_item_sal(self):
        antes = self.store.ingredientes()
        resultado = tools._resolver_ingredientes(self.store, [{"nome": "sal e pimenta a gosto", "quantidade": 5, "unidade": "g"}])
        self.assertTrue(resultado["pendencias"])
        self.assertNotIn("item_despensa", resultado["itens"][0])
        self.assertEqual(self.store.ingredientes(), antes)

    def test_evidencia_valida_independente_nao_se_perde_com_extras_inventados(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": 4}, "tecnicas": {"refogar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 60, "gas": "falta às vezes", "energia": "cai às vezes", "espaco_geladeira": "pouco"}})
        evidencia = "Tenho gás suficiente para preparar este lote."
        prato, = self.store.selecionar_pratos([{"nome": "Frango da casa"}])
        diz(evidencia)
        ficha = self.conferir(prato_id=prato["id"], confirmacoes_operacionais={"gas": evidencia, "energia": "Sim", "espaco_geladeira": "Sim"})
        self.assertTrue(ficha["rascunho_salvo"], ficha)
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual([c["chave"] for c in receita["dados"]["confirmacoes_operacionais"]], ["gas"])
        tipos = {b["tipo"] for b in ficha["bloqueios"]}
        self.assertNotIn("gas_a_confirmar", tipos)
        self.assertTrue({"energia_a_confirmar", "refrigeracao_a_confirmar"} <= tipos)
        self.assertEqual({r["chave"] for r in ficha["confirmacoes_recusadas"]}, {"energia", "espaco_geladeira"})
        self.assertEqual(self.store.perfil()["restricoes"]["gas"], "falta às vezes")

    def test_confirmacoes_em_formato_errado_nao_apagam_ficha(self):
        diz("Monte um prato para meu cardápio.")
        ficha = self.conferir(confirmacoes_operacionais=["sim", "sim", "sim"])
        self.assertTrue(ficha["rascunho_salvo"], ficha)
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual(ficha["confirmacoes_recusadas"][0]["chave"], "confirmacoes_operacionais")
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_montagem_delegada_completa_a_fonte_sem_confirmar_cozinha_desconhecida(self):
        diz("Monte um prato para meu cardápio.")
        original = {"nome": "Frango com quiabo", "url": "https://exemplo.com/frango-quiabo",
                    "porcoes": 4, "tempo_preparo_min": 45,
                    "ingredientes": ["500 g de peito de frango", "200 g de quiabo", "50 g de cebola"],
                    "modo_preparo": "Refogue a cebola, junte o frango e cozinhe. Acrescente o quiabo e termine o preparo no fogão."}
        pesquisa.marcar_turno(SESSAO, "monte um prato para meu cardápio", conduzida=True)
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [{
            "url": original["url"], "title": original["nome"], "receitas": [original], "content": "# Frango com quiabo\n## Ingredientes\n500 g de peito de frango\n## Modo de preparo\nRefogue no fogão."}]}), session_id=SESSAO)
        ficha = self.conferir(nome=original["nome"])
        self.assertTrue(ficha["rascunho_salvo"], ficha)
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertTrue(ficha["ficha_original_da_fonte"])
        self.assertNotIn("fonte_incompleta", {b["tipo"] for b in ficha["bloqueios"]})
        self.assertEqual(len(self.store.obter_receita(ficha["receita_id"])["dados"]["ingredientes"]), 3)
        self.assertEqual(self.store.obter_receita(ficha["receita_id"])["dados"]["fonte_verificada"], original)
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_conferencia_sem_id_recupera_vinculo_do_rascunho_escolhido_por_nome(self):
        primeiro, segundo = self.store.selecionar_pratos([{"nome": "Frango da casa"}, {"nome": "Frango com quiabo"}])
        diz("Confira os ingredientes da primeira receita escolhida.")
        montagem.iniciar_turno(SESSAO, "vinculo-por-nome")
        ficha = self.conferir()
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["prato_id"], primeiro["id"])
        self.assertTrue(ficha["rascunho_salvo"])
        self.assertEqual(self.store.obter_receita(ficha["receita_id"])["dados"]["prato_id"], primeiro["id"])
        self.assertEqual(self.store.obter_prato(primeiro["id"])["receita_id"], ficha["receita_id"])
        self.assertIsNone(self.store.obter_prato(segundo["id"])["receita_id"])
        pronta = montagem.resposta_preparada(SESSAO, "vinculo-por-nome")
        self.assertIsNotNone(pronta)
        self.assertIn(f"#{primeiro['id']}", pronta)
        self.assertNotIn("#None", pronta)
        self.assertEqual(len(self.store.pratos()), 2)
        self.assertTrue(all(p["status"] == "selecionado" and p["preco_venda"] is None for p in self.store.pratos()))
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def alvo_conduzido(self):
        primeiro, segundo = self.store.selecionar_pratos([
            {"nome": "Frango da casa", "fonte_url": "https://exemplo.com/frango-da-casa"},
            {"nome": "Frango com quiabo", "fonte_url": "https://exemplo.com/frango-quiabo"}])
        diz("Quero esses dois, mantenha os pratos separados.")
        pesquisa.conduzir_escolhido(SESSAO, primeiro)
        fonte = {"nome": primeiro["nome"], "url": primeiro["fonte_url"], "porcoes": 4, "tempo_preparo_min": 45,
                 "ingredientes": ["500 g de peito de frango"], "modo_preparo": "Refogue o frango e cozinhe até ficar pronto no fogão."}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [{
            "url": fonte["url"], "title": fonte["nome"], "receitas": [fonte],
            "content": "# Frango da casa\n## Ingredientes\n500 g de peito de frango\n## Modo de preparo\nRefogue o frango e cozinhe no fogão."}]}), session_id=SESSAO)
        return primeiro, segundo, fonte

    def test_nome_id_e_fonte_omitidos_usam_alvo_inequivoco_da_conducao(self):
        primeiro, segundo, fonte = self.alvo_conduzido()
        montagem.iniciar_turno(SESSAO, "alvo-sem-argumentos")
        ficha = json.loads(tools.receita_conferir_ingredientes({
            "ingredientes": [{"nome": "peito de frango", "quantidade": 500, "unidade": "g"}],
            "equipamentos_necessarios": ["fogão"], "tecnicas_necessarias": ["refogar"]}, session_id=SESSAO))
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["prato_id"], primeiro["id"])
        self.assertEqual(ficha["receita"], primeiro["nome"])
        self.assertTrue(ficha["rascunho_salvo"])
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual(receita["fonte_url"], primeiro["fonte_url"])
        self.assertEqual(receita["dados"]["fonte_verificada"], fonte)
        self.assertIsNone(self.store.obter_prato(segundo["id"])["receita_id"])
        self.assertNotIn("#None", montagem.resposta_preparada(SESSAO, "alvo-sem-argumentos"))
        self.assertEqual(self.store.obter_prato(primeiro["id"])["status"], "selecionado")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_alvo_da_conducao_nao_corrige_argumento_contraditorio_nem_vaza_para_outro_chat(self):
        primeiro, segundo, _ = self.alvo_conduzido()
        argumentos = {"ingredientes": [{"nome": "peito de frango", "quantidade": 500, "unidade": "g"}]}
        for errado in ({"prato_id": segundo["id"]}, {"nome": "Um prato que não foi escolhido"}, {"fonte_url": segundo["fonte_url"]},
                       {"prato_id": primeiro["id"], "fonte_url": segundo["fonte_url"]}):
            with self.subTest(errado=errado):
                resposta = json.loads(tools.receita_conferir_ingredientes({**argumentos, **errado}, session_id=SESSAO))
                self.assertFalse(resposta["success"], resposta)
        outra = json.loads(tools.receita_conferir_ingredientes(argumentos, session_id="outro-chat"))
        self.assertFalse(outra["success"], outra)
        self.assertEqual(self.store.receitas(), [])
        self.assertEqual([p["id"] for p in self.store.pratos()], [primeiro["id"], segundo["id"]])
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_selecao_integral_monta_medidas_publicadas_sem_redigitacao_da_llm(self):
        primeiro, segundo, fonte = self.alvo_conduzido()
        fonte = {**fonte, "ingredientes": ["500 g de peito de frango", "1 colher de sopa de limão", "Sal e pimenta a gosto"]}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [{
            "url": fonte["url"], "title": fonte["nome"], "receitas": [fonte]}]}), session_id=SESSAO)
        lista = (f"### 1. {primeiro['nome']}\nFonte: {primeiro['fonte_url']}\nIngredientes confirmados: frango\n"
                 f"### 2. {segundo['nome']}\nFonte: {segundo['fonte_url']}\nIngredientes confirmados: frango e quiabo")
        falas.registrar(SESSAO, [{"role": "assistant", "content": lista},
                                {"role": "user", "content": "quero esses dois"}])
        ficha = json.loads(tools.receita_conferir_ingredientes({"adaptacao": "não há", "ingredientes": [
            {"nome": "peito de frango", "quantidade": 99, "unidade": "kg"},
            {"nome": "limão", "quantidade": 1, "unidade": "un"}]}, session_id=SESSAO))
        self.assertTrue(ficha["success"], ficha)
        self.assertTrue(ficha["ficha_original_da_fonte"])
        dados = self.store.obter_receita(ficha["receita_id"])["dados"]
        self.assertEqual(dados["ingredientes"][0]["quantidade"], 500)
        self.assertEqual(dados["ingredientes"][1]["unidade"], "colher de sopa")
        self.assertNotIn("quantidade", dados["ingredientes"][2])
        self.assertFalse(dados["fidelidade_fonte"]["diferencas"])
        self.assertIsNone(dados["adaptacao"])
        self.assertEqual(self.store.obter_prato(primeiro["id"])["status"], "selecionado")
        self.assertIsNone(self.store.obter_prato(segundo["id"])["receita_id"])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_modificacao_explicita_nao_e_sobrescrita_pela_receita_original(self):
        primeiro, segundo, fonte = self.alvo_conduzido()
        lista = (f"### 1. {primeiro['nome']}\nFonte: {primeiro['fonte_url']}\nIngredientes confirmados: frango\n"
                 f"### 2. {segundo['nome']}\nFonte: {segundo['fonte_url']}\nIngredientes confirmados: frango e quiabo")
        falas.registrar(SESSAO, [{"role": "assistant", "content": lista},
                                {"role": "user", "content": "quero esses dois, mas faça metade da receita"}])
        ficha = json.loads(tools.receita_conferir_ingredientes({"adaptacao": "Metade do lote original, mantendo os ingredientes.",
            "ingredientes": [{"nome": "peito de frango", "quantidade": 250, "unidade": "g"}],
            "porcoes": 2, "tempo_preparo_min": 45, "modo_preparo": "Refogue a metade do frango na panela."}, session_id=SESSAO))
        self.assertTrue(ficha["success"], ficha)
        self.assertFalse(ficha["ficha_original_da_fonte"])
        dados = self.store.obter_receita(ficha["receita_id"])["dados"]
        self.assertEqual(dados["ingredientes"][0]["quantidade"], 250)
        self.assertTrue(dados["adaptacao"])

    def test_retoma_fonte_salva_corrige_medidas_sem_duplicar_nem_inventar_temperos(self):
        original = {"nome": "Frango com Batata Fácil", "url": "https://receitas.exemplo/frango-batata",
                    "porcoes": 4, "tempo_preparo_min": None,
                    "ingredientes": ["1/2 cebola (picada)", "1/2 Xícara (chá de água)", "Sal", "Azeite a gosto"],
                    "modo_preparo": "Refogue a cebola no azeite, junte água e cozinhe por cerca de 30 minutos."}
        prato, = self.store.selecionar_pratos([{"nome": original["nome"], "fonte_url": original["url"]}])
        errados = [{"nome": "cebola", "quantidade": 0.5, "unidade": "kg"},
                   {"nome": "agua", "quantidade": 0.5, "unidade": "L"}, {"nome": "Sal", "quantidade": 1, "unidade": "kg"}]
        receita_id = self.store.salvar_receita(original["nome"], original["url"], 4, {
            "ingredientes": errados, "fonte_verificada": original, "adaptacao": None,
            "fidelidade_fonte": fonte.conferir(original, errados, 4), "tempo_preparo_min": 32,
            "equipamentos_necessarios": ["fogão"], "tecnicas_necessarias": ["refogar"]}, "conferida", prato_id=prato["id"])
        prato = self.store.obter_prato(prato["id"])
        antes = self.store.ingredientes(), self.store.perfil(), self.store.orcamento()
        diz(f"Quero continuar o prato escolhido #{prato['id']} ({prato['nome']}).")
        self.assertTrue(pesquisa.conduzir_escolhido(SESSAO, prato,
            receita=self.store.obter_receita(receita_id), revisar_fonte=True))
        montagem.iniciar_turno(SESSAO, "reparar-fonte")
        ficha = json.loads(tools.receita_conferir_ingredientes({"prato_id": prato["id"], "ingredientes": errados,
            "fonte_url": "https://exemplo.com/endereco-inventado"}, session_id=SESSAO))
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["prato_id"], prato["id"])
        self.assertEqual(len(self.store.pratos()), 1)
        self.assertEqual(self.store.obter_prato(prato["id"])["receita_id"], ficha["receita_id"])
        self.assertEqual(self.store.obter_receita(receita_id)["dados"]["ingredientes"], errados)
        dados = self.store.obter_receita(ficha["receita_id"])["dados"]
        self.assertEqual(self.store.obter_receita(ficha["receita_id"])["fonte_url"], original["url"])
        self.assertTrue(dados["ficha_original_da_fonte"])
        self.assertFalse(dados["fidelidade_fonte"]["diferencas"])
        self.assertEqual(dados["ingredientes"][0], {"nome": "cebola picada", "quantidade": 0.5, "unidade": "un"})
        self.assertEqual(dados["ingredientes"][1], {"nome": "agua", "quantidade": 0.5, "unidade": "xicara cha"})
        self.assertEqual(dados["ingredientes"][2:], [{"nome": "Sal"}, {"nome": "Azeite a gosto"}])
        self.assertIsNone(dados["tempo_preparo_min"])
        self.assertEqual((self.store.ingredientes(), self.store.perfil(), self.store.orcamento()), antes)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(montagem.resposta_preparada(SESSAO, "reparar-fonte").count("?"), 1)
