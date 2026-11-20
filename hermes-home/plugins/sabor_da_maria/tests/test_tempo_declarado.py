"""Tempo disponível vem da declaração da pessoa, nunca da duração da receita."""

import unittest

from sabor_da_maria import falas, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


PERGUNTA = "Quanto tempo você consegue dedicar a esta cozinhada?"


def fala(texto, pergunta=""):
    historico = [{"role": "assistant", "content": pergunta}] if pergunta else []
    return falas.extrair(historico, texto)[-1]


class TempoDeclaradoTests(unittest.TestCase):
    def test_limites_explicitos_em_minutos_e_horas(self):
        for texto, esperado in (
            ("Tenho até 1 hora por cozinhada. Continue o prato #1.", 60),
            ("Tenho uma hora e meia por cozinhada.", 90),
            ("Tenho meia hora para cozinhar.", 30),
            ("Tenho 45min por cozinhada.", 45),
            ("Eu tenho no máximo 1,5 horas por cozinhada.", 90),
            ("Tenho 1.5 horas por cozinhada.", 90),
            ("Consigo dedicar duas horas e 30 minutos para cozinhar.", 150),
        ):
            with self.subTest(texto=texto):
                self.assertEqual(falas.tempo_disponivel_declarado(fala(texto)), esperado)

    def test_resposta_curta_depende_da_pergunta_real_sobre_disponibilidade(self):
        for texto, esperado in (("45 min", 45), ("uma hora e meia", 90), ("meia hora", 30),
                                ("até 1 hora", 60), ("Tenho 45 minutos", 45)):
            with self.subTest(texto=texto):
                self.assertIsNone(falas.tempo_disponivel_declarado(fala(texto)))
                self.assertIsNone(falas.tempo_disponivel_declarado(fala(texto, "Quanto tempo a receita leva?")))
                self.assertIsNone(falas.tempo_disponivel_declarado(fala(texto, "Você tem forno?")))
                self.assertEqual(falas.tempo_disponivel_declarado(fala(texto, PERGUNTA)), esperado)

    def test_negacoes_incertezas_ressalvas_e_tempo_de_receita_nao_sao_disponibilidade(self):
        for texto in (
            "A receita leva 45 minutos.", "O frango precisa de 1 hora para cozinhar.",
            "Não tenho 1 hora por cozinhada.", "Tenho talvez 1 hora por cozinhada.",
            "Tenho 1 hora por cozinhada, mas talvez menos hoje.",
            "Se eu tenho 1 hora por cozinhada, dá certo?", "Eu teria 1 hora por cozinhada.",
            "Tenho pelo menos 1 hora por cozinhada.", "Tenho entre 1 e 2 horas por cozinhada.",
            "Tenho 0 minutos por cozinhada.", "Tenho -45 minutos por cozinhada.",
            'Ela disse "tenho 1 hora por cozinhada".', '> Tenho 1 hora por cozinhada.',
            "Tenho 1 hora por cozinhada?", "45 min?", "Não sei, 45 min", "talvez 45 min",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(falas.tempo_disponivel_declarado(fala(texto, PERGUNTA)))

    def test_disponibilidades_conflitantes_nao_escolhem_um_valor(self):
        self.assertIsNone(falas.tempo_disponivel_declarado(
            fala("Tenho 1 hora por cozinhada. Tenho 2 horas por cozinhada.")))


class PersistenciaDeTempoTests(StoreTestCase):
    def test_merge_somente_do_tempo_sem_aprovar_prato_ou_comprometer_orcamento(self):
        store = Store()
        perfil = {"equipamentos": {"fogao": 4, "forno": False}, "tecnicas": {"refogar": "sim"},
                  "restricoes": {"gas": "botijão", "energia": "estável", "espaco_geladeira": "média",
                                 "tempo_por_cozinhada_min": 120}}
        store.perfil_salvar(perfil)
        perfil = store.perfil()
        prato, = store.selecionar_pratos([{"nome": "Frango com batata"}])
        atual = fala(f"Tenho até 1 hora por cozinhada. Continue o prato #{prato['id']}.")
        declarado = tools.atualizar_declaracoes_do_turno(store, atual)
        self.assertEqual(declarado, {"restricoes": {"tempo_por_cozinhada_min": 60}})
        perfil["restricoes"]["tempo_por_cozinhada_min"] = 60
        self.assertEqual(store.perfil(), perfil)
        self.assertEqual(store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.analises_preco(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_resposta_curta_salva_sem_precisar_da_llm_chamar_tool(self):
        store = Store()
        perfil = store.perfil()
        perfil["restricoes"]["tempo_por_cozinhada_min"] = 45
        declarado = tools.atualizar_declaracoes_do_turno(store, fala("45 min", PERGUNTA))
        self.assertEqual(declarado, {"restricoes": {"tempo_por_cozinhada_min": 45}})
        self.assertEqual(store.perfil(), perfil)
