"""Os botões de disponibilidade preservam faixas, sem inventar um limite exato."""

import json
import unittest

from sabor_da_maria import cozinha, falas, jornada, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


PERGUNTA = "Quanto tempo você consegue dedicar a esta cozinhada?"


def fala(texto, pergunta=""):
    return falas.extrair([{"role": "assistant", "content": pergunta}] if pergunta else [], texto)[-1]


class FaixasDeTempoTests(unittest.TestCase):
    def test_rotulos_e_payloads_distintos_somente_na_pergunta_de_disponibilidade(self):
        pergunta = jornada._pergunta({"id": 7}, [{"tipo": "tempo_disponivel_pendente", "detalhe": PERGUNTA}])
        self.assertEqual([o["rotulo"] for o in pergunta["opcoes"]], ["< 30 min", "1 hora", "> 2 horas", "> 6 horas"])
        for opcao, esperado in zip(pergunta["opcoes"], (
            {"operador": "menor_que", "minutos": 30}, None,
            {"operador": "maior_que", "minutos": 120}, {"operador": "maior_que", "minutos": 360},
        )):
            with self.subTest(rotulo=opcao["rotulo"]):
                # Mesmo payload do componente: a mensagem do botão inclui o
                # alvo e recebe a instrução de conduzir a próxima decisão.
                enviada = opcao["mensagem"] + " Continue o prato #7 (Frango com batata). Confira esta resposta e me conduza pela próxima decisão necessária."
                atual = fala(enviada)
                self.assertEqual(falas.tempo_disponivel_faixa_declarada(atual), esperado)
                self.assertEqual(falas.tempo_disponivel_declarado(atual), 60 if esperado is None else None)
        for tipo in ("tempo_da_receita_pendente", "tempo_disponivel_exato_pendente"):
            self.assertEqual(jornada._pergunta({"id": 7}, [{"tipo": tipo, "detalhe": "Informe o tempo exato."}])["opcoes"], [])

    def test_rotulo_digitado_exige_pergunta_real_e_nunca_vira_numero_exato(self):
        for texto, esperado in (("< 30 min", {"operador": "menor_que", "minutos": 30}),
                                ("> 2 horas", {"operador": "maior_que", "minutos": 120}),
                                ("> 6 horas", {"operador": "maior_que", "minutos": 360})):
            with self.subTest(texto=texto):
                self.assertIsNone(falas.tempo_disponivel_faixa_declarada(fala(texto)))
                self.assertEqual(falas.tempo_disponivel_faixa_declarada(fala(texto, PERGUNTA)), esperado)
                self.assertIsNone(falas.tempo_disponivel_declarado(fala(texto, PERGUNTA)))

    def test_negacoes_citacoes_e_hipoteses_nao_declaram_faixa(self):
        for texto in ("Não tenho mais de 2 horas por cozinhada.", "Tenho talvez mais de 2 horas por cozinhada.",
                      "Tenho mais de 2 horas por cozinhada?", 'Ela disse "Tenho mais de 2 horas por cozinhada".',
                      "> Tenho mais de 2 horas por cozinhada.", "Se eu tivesse mais de 2 horas por cozinhada.",
                      "A receita leva mais de 2 horas."):
            with self.subTest(texto=texto):
                self.assertIsNone(falas.tempo_disponivel_faixa_declarada(fala(texto, PERGUNTA)))

    def test_avaliacao_respeita_limites_estritos_e_pede_exato_so_quando_necessario(self):
        for operador, marco, duracao, esperado in (
            ("menor_que", 30, 30, "tempo_insuficiente"), ("menor_que", 30, 31, "tempo_insuficiente"),
            ("menor_que", 30, 20, "tempo_disponivel_exato_pendente"),
            ("maior_que", 120, 90, None), ("maior_que", 120, 120, None),
            ("maior_que", 120, 121, "tempo_disponivel_exato_pendente"),
            ("maior_que", 360, 360, None), ("maior_que", 360, 361, "tempo_disponivel_exato_pendente"),
        ):
            with self.subTest(operador=operador, marco=marco, duracao=duracao):
                perfil = {"restricoes": {"tempo_por_cozinhada_min": None,
                                         "tempo_por_cozinhada_faixa": {"operador": operador, "minutos": marco}}}
                resultado = tools._restricoes_relevantes(perfil, duracao, {}, [])
                tipos = [b["tipo"] for itens in resultado.values() for b in itens]
                self.assertEqual(tipos, [esperado] if esperado else [])

    def test_duracao_da_receita_ausente_nao_repete_disponibilidade_ja_respondida(self):
        perfil = {"restricoes": {"tempo_por_cozinhada_faixa": {"operador": "maior_que", "minutos": 120}}}
        resultado = tools._restricoes_relevantes(perfil, None, {}, [])
        self.assertEqual([b["tipo"] for itens in resultado.values() for b in itens], ["tempo_da_receita_pendente"])


class PersistenciaDeFaixasTests(StoreTestCase):
    def test_cliques_salvam_faixas_distintas_preservam_perfil_e_nao_aprovam(self):
        store = Store()
        store.perfil_salvar({"equipamentos": {"fogao": 4}, "tecnicas": {"refogar": "sim"},
                             "restricoes": {"gas": "botijão", "tempo_por_cozinhada_min": 90}})
        prato, = store.selecionar_pratos([{"nome": "Frango com batata"}])
        opcoes = jornada._pergunta(prato, [{"tipo": "tempo_disponivel_pendente", "detalhe": PERGUNTA}])["opcoes"]
        for opcao, minutos in ((opcoes[2], 120), (opcoes[3], 360), (opcoes[0], 30)):
            with self.subTest(rotulo=opcao["rotulo"]):
                esperado = {"operador": "menor_que" if minutos == 30 else "maior_que", "minutos": minutos}
                declarado = tools.atualizar_declaracoes_do_turno(store, fala(opcao["mensagem"]))
                self.assertEqual(declarado["restricoes"]["tempo_por_cozinhada_faixa"], esperado)
                self.assertIsNone(store.perfil()["restricoes"]["tempo_por_cozinhada_min"])
                self.assertEqual(store.perfil()["restricoes"]["tempo_por_cozinhada_faixa"], esperado)
                self.assertEqual(store.perfil()["restricoes"]["gas"], "botijão")
                self.assertEqual(store.perfil()["equipamentos"], {"fogao": 4})
        self.assertEqual(store.obter_prato(prato["id"])["status"], "selecionado")
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)
        tools.atualizar_declaracoes_do_turno(store, fala(opcoes[1]["mensagem"]))
        self.assertEqual(store.perfil()["restricoes"]["tempo_por_cozinhada_min"], 60)
        self.assertIsNone(store.perfil()["restricoes"]["tempo_por_cozinhada_faixa"])

    def test_editar_outro_campo_ou_reenviar_tempo_vazio_preserva_faixa(self):
        store = Store()
        tools.atualizar_declaracoes_do_turno(store, fala("Tenho mais de 2 horas por cozinhada."))
        esperado = {"operador": "maior_que", "minutos": 120}
        store.cozinha_salvar({"rotina": {"gas": "encanado", "tempo_por_cozinhada_min": None}})
        self.assertEqual(store.perfil()["restricoes"]["tempo_por_cozinhada_faixa"], esperado)
        self.assertEqual(store.cozinha()["outros"], [{"grupo": "restricoes", "chave": "tempo_por_cozinhada_faixa",
                                                     "nome": "Tempo por cozinhada", "texto": "Mais de 2 horas"}])
        self.assertNotIn("tempo_por_cozinhada_min", [p["chave"] for p in tools._pendencias_perfil(store.perfil())])
        store.cozinha_salvar({"rotina": {"tempo_por_cozinhada_min": 60}})
        self.assertEqual(store.perfil()["restricoes"]["tempo_por_cozinhada_min"], 60)
        self.assertNotIn("tempo_por_cozinhada_faixa", store.perfil()["restricoes"])
        self.assertEqual(store.cozinha()["outros"], [])

    def test_llm_nao_pode_converter_faixa_em_limite_exato(self):
        store = Store()
        atual = "Tenho mais de 2 horas por cozinhada."
        falas.registrar(SESSAO, [], atual)
        tools.atualizar_declaracoes_do_turno(store, fala(atual))
        resultado = json.loads(tools.HANDLERS["perfil_cozinha_salvar"](
            {"restricoes": {"tempo_por_cozinhada_min": 120}}, session_id=SESSAO))
        self.assertFalse(resultado["success"], resultado)
        self.assertIsNone(store.perfil()["restricoes"]["tempo_por_cozinhada_min"])
        self.assertEqual(store.perfil()["restricoes"]["tempo_por_cozinhada_faixa"],
                         {"operador": "maior_que", "minutos": 120})
