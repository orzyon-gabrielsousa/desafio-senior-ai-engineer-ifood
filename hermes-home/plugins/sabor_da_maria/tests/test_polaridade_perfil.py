"""A menção de um equipamento ou técnica não autoriza inverter a resposta."""

import json

from sabor_da_maria import falas, tools
from sabor_da_maria.store import Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


def salvar(**args):
    return json.loads(tools.perfil_cozinha_salvar(args, session_id=SESSAO))


class PolaridadePerfilTests(StoreTestCase):
    def test_negacao_de_forno_e_massa_nao_vira_sim(self):
        diz("Não tenho forno. Nunca fiz massa fresca.")
        out = salvar(equipamentos={"forno": True}, tecnicas={"massa fresca": "sim"})
        self.assertTrue(out["success"], out)
        self.assertEqual(len(out["correcoes"]), 2)
        self.assertFalse(Store().perfil()["equipamentos"]["forno"])
        self.assertEqual(Store().perfil()["tecnicas"]["massa_fresca"], "nao")
        corretos = salvar(equipamentos={"forno": False}, tecnicas={"massa fresca": "nao"})
        self.assertTrue(corretos["success"], corretos)
        self.assertFalse(corretos["perfil"]["equipamentos"]["forno"])
        self.assertEqual(corretos["perfil"]["tecnicas"]["massa_fresca"], "nao")

    def test_afirmacao_nao_vira_nao(self):
        diz("Tenho forno e sei fazer massa fresca.")
        out = salvar(equipamentos={"forno": False}, tecnicas={"massa fresca": "nao"})
        self.assertTrue(out["success"], out)
        self.assertEqual(len(out["correcoes"]), 2)
        self.assertTrue(Store().perfil()["equipamentos"]["forno"])
        self.assertEqual(Store().perfil()["tecnicas"]["massa_fresca"], "sim")

    def test_clausulas_separadas_nao_compartilham_negacao(self):
        for texto in ("Não tenho air fryer, tenho forno.", "Tenho forno e não tenho air fryer.",
                      "Não tenho air fryer. Tenho forno."):
            with self.subTest(texto=texto):
                diz(texto)
                out = salvar(equipamentos={"forno": True, "air fryer": False})
                self.assertTrue(out["success"], out)
                self.assertNotIn("recusados", out)

    def test_lista_de_equipamentos_e_tecnicas_com_estados_diferentes(self):
        diz("Não tenho forno nem air fryer. Tenho liquidificador, panela de pressão e frigideira. "
            "Sei refogar. Nunca fiz massa fresca e molho branco estou aprendendo.")
        out = salvar(equipamentos={"forno": False, "air fryer": False, "liquidificador": True,
                                    "panela de pressão": True, "frigideira": True},
                     tecnicas={"refogado": "sim", "massa fresca": "nao", "molho branco": "parcial"})
        self.assertTrue(out["success"], out)
        self.assertNotIn("recusados", out)

    def test_resposta_curta_usa_pergunta_unica_sem_inverter(self):
        for resposta, valor in (("não", False), ("sim", True), ("tenho sim", True)):
            with self.subTest(resposta=resposta):
                falas.registrar(SESSAO, [{"role": "assistant", "content": "Você tem forno funcionando?"}], resposta)
                out = salvar(equipamentos={"forno": not valor})
                self.assertTrue(out["success"], out)
                self.assertIs(Store().perfil()["equipamentos"]["forno"], valor)
                self.assertEqual(len(out["correcoes"]), 1)

    def test_aprendendo_nao_vira_dominio_completo(self):
        falas.registrar(SESSAO, [{"role": "assistant", "content": "Você sabe fazer massa fresca?"}], "estou aprendendo")
        out = salvar(tecnicas={"massa fresca": "sim"})
        self.assertTrue(out["success"], out)
        self.assertEqual(Store().perfil()["tecnicas"]["massa_fresca"], "parcial")

    def test_resposta_ambigua_ou_pergunta_nao_vira_cadastro(self):
        for texto in ("Talvez eu tenha forno.", "Não sei se tenho forno.", "Tenho e não tenho forno.", "Tem forno?"):
            for valor in (True, False):
                with self.subTest(texto=texto, valor=valor):
                    diz(texto)
                    self.assertFalse(salvar(equipamentos={"forno": valor})["success"])
        falas.registrar(SESSAO, [{"role": "assistant", "content": "Você tem forno e air fryer?"}], "sim")
        out = salvar(equipamentos={"forno": True, "air fryer": True})
        self.assertFalse(out["success"])
        self.assertEqual(len(out["recusados"]), 2)

    def test_alteracao_posterior_prevalece_sobre_fala_antiga(self):
        diz("Não tenho forno.")
        self.assertTrue(salvar(equipamentos={"forno": False})["success"])
        diz("Não tenho forno.", "Agora tenho forno.")
        out = salvar(equipamentos={"forno": False})
        self.assertTrue(out["success"], out)
        self.assertEqual(len(out["correcoes"]), 1)
        self.assertTrue(Store().perfil()["equipamentos"]["forno"])

    def test_numero_antigo_nao_substitui_contagem_atual(self):
        diz("Meu fogão tem 4 bocas.", "Agora meu fogão tem 2 bocas.")
        self.assertFalse(salvar(equipamentos={"fogao": 4})["success"])
        self.assertTrue(salvar(equipamentos={"fogao": 2})["success"])

    def test_equipamento_quebrado_nao_e_disponivel(self):
        for texto in ("Tenho forno mas ele não funciona.", "Não tenho forno funcionando.", "Meu forno não está funcionando."):
            with self.subTest(texto=texto):
                diz(texto)
                out = salvar(equipamentos={"forno": True})
                self.assertTrue(out["success"], out)
                self.assertFalse(Store().perfil()["equipamentos"]["forno"])

    def test_aquisicao_explicita_atualiza_disponibilidade_sem_adivinhar_futuro(self):
        diz("Comprei um forno novo.")
        self.assertTrue(salvar(equipamentos={"forno": True})["success"])
        diz("Pretendo comprar forno.")
        self.assertFalse(salvar(equipamentos={"forno": True})["success"])

    def perfil_completo(self):
        store = Store()
        store.perfil_salvar({"equipamentos": {"fogao": 4, "forno": True, "panela_pressao": True,
                                              "air_fryer": True, "liquidificador": True, "forma": True},
                             "tecnicas": {"assar": "sim", "refogar": "sim"},
                             "restricoes": {"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120,
                                            "gas": "botijão", "energia": "estável"}})
        return store

    def conferir(self, **extras):
        args = {"nome": "Frango assado", "porcoes": 4, "tempo_preparo_min": 30,
                "equipamentos_necessarios": ["forno"], "tecnicas_necessarias": ["assar"],
                "ingredientes": [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}], **extras}
        return json.loads(tools.receita_conferir_ingredientes(args, session_id=SESSAO))

    def test_negativa_corrige_perfil_antigo_e_bloqueia_cmv_e_registro(self):
        store = self.perfil_completo()
        diz(FALA_COZINHA)
        ficha = self.conferir()
        cmv = json.loads(tools.prato_calcular_cmv({"receita_id": ficha["receita_id"]}, session_id=SESSAO))
        self.assertTrue(cmv["success"], cmv)
        diz("Agora não tenho forno. Nunca fiz massa fresca.")
        out = salvar(equipamentos={"forno": True}, tecnicas={"massa fresca": "sim"})
        self.assertTrue(out["success"], out)
        self.assertFalse(store.perfil()["equipamentos"]["forno"])
        self.assertEqual(len(out["correcoes"]), 2)
        self.assertFalse(json.loads(tools.prato_calcular_cmv({"receita_id": ficha["receita_id"]}, session_id=SESSAO))["success"])
        registro = json.loads(tools.cardapio_registrar({"prato_id": cmv["prato_id"], "preco_escolhido": 10}, session_id=SESSAO))
        self.assertFalse(registro["success"], registro)
        self.assertEqual(store.reservas_ativas(), [])
        diz("Comprei um forno novo, agora tenho forno funcionando.")
        self.assertTrue(salvar(equipamentos={"forno": False})["success"])
        self.assertTrue(store.perfil()["equipamentos"]["forno"])
        self.assertEqual(self.conferir()["veredito"], "viavel")

    def test_gas_acabou_e_energia_caindo_nao_preservam_rotina_estavel_antiga(self):
        store = self.perfil_completo()
        diz("O gás acabou e a energia está caindo.")
        out = salvar(restricoes={"gas": "botijão", "energia": "estável"})
        self.assertTrue(out["success"], out)
        self.assertEqual(out["salvo"]["restricoes"], {"gas": "sem gás", "energia": "cai às vezes"})
        self.assertEqual(len(out["correcoes"]), 2)
        ficha = self.conferir(equipamentos_necessarios=["forno a gás"])
        self.assertNotEqual(ficha["veredito"], "viavel")
        tipos = {b["tipo"] for b in ficha["bloqueios"]}
        self.assertIn("gas_indisponivel", tipos)
        self.assertIn("energia_a_confirmar", tipos)
        self.assertEqual(store.reservas_ativas(), [])

    def test_resposta_curta_gas_acabou_corrige_campo_pedido_sem_preencher_outros(self):
        store = self.perfil_completo()
        falas.registrar(SESSAO, [{"role": "assistant", "content": "Seu gás dá conta do preparo?"}], "Acabou.")
        out = salvar(restricoes={"gas": "botijão"})
        self.assertTrue(out["success"], out)
        self.assertEqual(store.perfil()["restricoes"]["gas"], "sem gás")
        self.assertEqual(store.perfil()["restricoes"]["energia"], "estável")
        self.assertEqual(set(out["salvo"]["restricoes"]), {"gas"})

    def test_incerteza_explicita_marca_valor_antigo_pendente_sem_inventar_novo(self):
        store = self.perfil_completo()
        diz("Não sei se tenho forno funcionando. Não sei se a energia está estável.")
        out = salvar(equipamentos={"forno": True}, restricoes={"energia": "estável"})
        self.assertFalse(out["success"], out)
        self.assertIsNone(store.perfil()["equipamentos"]["forno"])
        self.assertIsNone(store.perfil()["restricoes"]["energia"])
        self.assertIn("marcados_pendentes", out)
        self.assertNotEqual(self.conferir()["veredito"], "viavel")

    def test_negativa_curta_a_duas_condicoes_nao_reutiliza_disponibilidade_antiga(self):
        store = self.perfil_completo()
        falas.registrar(SESSAO, [{"role": "assistant", "content": "Você tem gás e energia suficientes?"}], "Não tenho.")
        out = salvar(restricoes={"gas": "botijão", "energia": "estável"})
        self.assertFalse(out["success"], out)
        self.assertIsNone(store.perfil()["restricoes"]["gas"])
        self.assertIsNone(store.perfil()["restricoes"]["energia"])
        self.assertNotEqual(self.conferir()["veredito"], "viavel")

    def test_declaracao_atual_e_salva_sem_tool_e_sem_reaproveitar_historico(self):
        store = self.perfil_completo()
        alterados = tools.atualizar_declaracoes_do_turno(store, falas.Fala(
            "Sim, aceito preparar o prato #1, mas não tenho forno."))
        self.assertEqual(alterados, {"equipamentos": {"forno": False}})
        self.assertFalse(store.perfil()["equipamentos"]["forno"])
        repetida = tools.atualizar_declaracoes_do_turno(store, falas.Fala("Não tenho forno."))
        self.assertEqual(repetida, {"equipamentos": {"forno": False}})
        self.assertEqual(store.perfil()["equipamentos"]["fogao"], 4)
        self.assertEqual(store.perfil()["restricoes"]["energia"], "estável")
        self.assertEqual(tools.atualizar_declaracoes_do_turno(store, falas.Fala("Sim.")), {})
        self.assertFalse(store.perfil()["equipamentos"]["forno"])

    def test_declaracao_automatica_ignora_citacao_pergunta_hipotese_e_terceiros(self):
        store = self.perfil_completo()
        antes = store.perfil()
        for texto in ('A receita diz: "Não tenho forno".', "Ela disse que não tem forno.",
                      "Se eu não tenho forno, como faria?", "Caso eu não tenha forno, adapte o prato.",
                      "Por exemplo, não tenho forno.", "Você tem forno?", "> Não tenho forno.",
                      "Tenho arroz, tomate e cebola.", "Tenho forno se o vizinho emprestar.",
                      "Tenho vontade de comprar forno."):
            with self.subTest(texto=texto):
                self.assertEqual(tools.atualizar_declaracoes_do_turno(store, falas.Fala(texto)), {})
                self.assertEqual(store.perfil(), antes)

    def test_declaracao_atual_separa_predicados_e_respeita_funcionamento(self):
        store = self.perfil_completo()
        for texto in ("Não tenho air fryer, tenho forno.", "Tenho forno mas ele não funciona.",
                      "Meu forno não funciona."):
            tools.atualizar_declaracoes_do_turno(store, falas.Fala(texto))
        self.assertFalse(store.perfil()["equipamentos"]["air_fryer"])
        self.assertFalse(store.perfil()["equipamentos"]["forno"])
        tools.atualizar_declaracoes_do_turno(store, falas.Fala("Agora tenho forno funcionando."))
        self.assertTrue(store.perfil()["equipamentos"]["forno"])

    def test_declaracao_atual_preserva_incerteza_e_recursos_indisponiveis(self):
        store = self.perfil_completo()
        alterados = tools.atualizar_declaracoes_do_turno(store, falas.Fala(
            "Não sei se tenho forno funcionando. O gás acabou e a energia está caindo."))
        self.assertIsNone(alterados["equipamentos"]["forno"])
        self.assertEqual(alterados["restricoes"], {"gas": "sem gás", "energia": "cai às vezes"})
        store.perfil_salvar({"restricoes": {"gas": "botijão"}})
        tools.atualizar_declaracoes_do_turno(store, falas.Fala("Sim, aceito o prato #1, só que acabou o gás."))
        self.assertEqual(store.perfil()["restricoes"]["gas"], "sem gás")
        store.perfil_salvar({"equipamentos": {"forno": True}})
        tools.atualizar_declaracoes_do_turno(store, falas.Fala("Tenho e não tenho forno."))
        self.assertIsNone(store.perfil()["equipamentos"]["forno"])
