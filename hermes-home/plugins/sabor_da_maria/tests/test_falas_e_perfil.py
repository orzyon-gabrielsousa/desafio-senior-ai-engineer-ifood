"""Trava de evidência: o perfil da cozinha só guarda o que a Dona Maria disse."""

import json
import logging
import os
import unittest

import sabor_da_maria
from sabor_da_maria import falas, tools

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


def salvar(**args):
    return json.loads(tools.HANDLERS["perfil_cozinha_salvar"](args, session_id=SESSAO))


def clarify(pergunta, resposta, opcoes=("Sim", "Não")):
    return json.dumps({"responses": [{"question": pergunta, "choices_offered": list(opcoes), "status": "answered", "user_response": resposta}],
                       "outcome": "submitted"}, ensure_ascii=False)


class ExtracaoTests(unittest.TestCase):
    def test_mensagens_e_respostas_do_clarify(self):
        historico = [
            {"role": "system", "content": "regras da consultora"},
            {"role": "user", "content": "Oi, sou a Dona Maria."},
            {"role": "assistant", "content": "Você tem forno?"},
            {"role": "user", "content": "tenho sim"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": clarify("Tem air fryer?", "Não")},
            {"role": "tool", "tool_call_id": "2", "content": json.dumps({"success": True, "resultados": []})},
            {"role": "session_meta", "content": None},
        ]
        lista = falas.extrair(historico)
        self.assertEqual([f.texto for f in lista], ["Oi, sou a Dona Maria.", "tenho sim", "Não"])
        self.assertEqual(lista[0].pergunta, "")
        self.assertEqual(lista[1].pergunta, "Você tem forno?")
        self.assertEqual(lista[2].pergunta, "Tem air fryer?")

    def test_resposta_longa_nao_herda_a_pergunta(self):
        longa = "Tenho fogão de quatro bocas e uma geladeira grande aqui em casa"
        lista = falas.extrair([{"role": "assistant", "content": "Você tem forno?"}, {"role": "user", "content": longa}])
        self.assertEqual(lista[0].pergunta, "")
        self.assertFalse(falas.apoia(["forno"], lista))

    def test_mensagem_do_turno_entra_sem_duplicar(self):
        self.assertEqual([f.texto for f in falas.extrair([], "Tenho forno")], ["Tenho forno"])
        historico = [{"role": "user", "content": "Tenho forno"}]
        self.assertEqual(len(falas.extrair(historico, "Tenho forno")), 1)

    def test_conteudo_em_partes(self):
        historico = [{"role": "user", "content": [{"type": "text", "text": "Tenho"}, {"type": "text", "text": "forno"}]}]
        self.assertEqual(falas.extrair(historico)[0].texto, "Tenho forno")


class ApoioTests(unittest.TestCase):
    def setUp(self):
        self.dela = falas.extrair([{"role": "user", "content": "Faço molhos brancos e assados, mas massa fresca nunca fiz. Uso a panela de pressão."}])

    def test_plural_genero_e_variacao(self):
        for termo in ("molho branco", "molhos brancos", "assados", "assar", "massa fresca", "pressão"):
            self.assertTrue(falas.apoia([termo], self.dela), termo)

    def test_precisa_de_todas_as_palavras(self):
        for termo in ("ponto de carne", "fritura", "forno"):
            self.assertFalse(falas.apoia([termo], self.dela), termo)

    def test_sinonimos_sao_alternativas(self):
        self.assertTrue(falas.apoia(["air fryer", "fritadeira sem oleo"], falas.extrair([{"role": "user", "content": "Tenho uma fritadeira sem óleo"}])))

    def test_numeros_em_digito_e_por_extenso(self):
        dela = falas.extrair([{"role": "user", "content": "Meu fogão tem quatro bocas e tenho 2 horas por cozinhada"}])
        self.assertTrue(falas.apoia_numero(4, dela))
        self.assertFalse(falas.apoia_numero(5, dela))
        self.assertTrue(falas.apoia_numero(120, dela, minutos=True))
        self.assertFalse(falas.apoia_numero(90, dela, minutos=True))
        self.assertFalse(falas.apoia_numero(120, dela))  # fora de minutos, 120 não é 2

    def test_cobertura_de_observacao(self):
        dela = falas.extrair([{"role": "user", "content": "O gás é de botijão e a energia é estável"}])
        self.assertEqual(falas.cobertura("gás de botijão e energia estável", dela), 1.0)
        self.assertLess(falas.cobertura("trabalhou em restaurante francês", dela), 0.6)


class PerfilComFalasTests(StoreTestCase):
    def test_o_que_ela_disse_entra(self):
        diz(FALA_COZINHA)
        out = salvar(
            equipamentos={"fogão": 4, "forno": True, "panela de pressão": True, "air fryer": False, "liquidificador": True},
            restricoes={"espaco_geladeira": "média", "tempo_por_cozinhada_min": 120, "gas": "botijão ok", "energia": "estável"},
            tecnicas={"refogado": "sim", "massa fresca": "nao"},
        )
        self.assertTrue(out["success"], out)
        self.assertNotIn("recusados", out)
        self.assertTrue(out["completo"], out["pendencias"])
        self.assertEqual(out["perfil"]["equipamentos"]["fogao"], 4)

    def test_incidente_da_demo_o_modelo_inventou_tecnicas_e_utensilios(self):
        """14/11/2026, passo 4 do roteiro: o Qwen3-8B gravou técnicas e utensílios que ela nunca citou."""
        diz("Gosto de fazer frango e carne moída, para marmita.",
            "Tenho fogão de 4 bocas, forno, panela de pressão e liquidificador. Não tenho air fryer. "
            "A geladeira é média, tenho até 2 horas por cozinhada, gás no botijão e a energia é estável.")
        observacao = "Dona Maria tem gás de botijão e energia estável. Usa panela de pressão e liquidificador. Não tem air fryer."
        out = salvar(
            equipamentos={"fogao": 4, "forno": True, "panela_pressao": True, "air_fryer": False, "liquidificador": True,
                          "escorredor": True, "faca": True, "tigela": True},
            tecnicas={"massa_fresca": "nao", "molho_branco": "sim", "ponto_de_carne": "aprendendo", "assados": "sim"},
            restricoes={"espaco_geladeira": "média", "tempo_por_cozinhada_min": 120, "gas": "botijão ok", "energia": "estável"},
            observacoes=observacao,
        )
        self.assertTrue(out["success"], out)  # o que ela disse entrou
        self.assertFalse(out["bloqueado"] if "bloqueado" in out else False)
        self.assertEqual({(r["grupo"], r["chave"]) for r in out["recusados"]},
                         {("equipamentos", "escorredor"), ("equipamentos", "faca"), ("equipamentos", "tigela"),
                          ("tecnicas", "massa_fresca"), ("tecnicas", "molho_branco"), ("tecnicas", "ponto_de_carne"), ("tecnicas", "assados")})
        perfil = out["perfil"]
        self.assertEqual(perfil["tecnicas"], {})
        self.assertEqual(sorted(perfil["equipamentos"]), ["air_fryer", "fogao", "forno", "liquidificador", "panela_pressao"])
        self.assertEqual(perfil["restricoes"]["tempo_por_cozinhada_min"], 120)
        self.assertEqual(perfil["observacoes"], [observacao])
        # Sem técnica informada o perfil não fecha: é a trava que faz a consultora perguntar em vez de supor.
        self.assertFalse(out["completo"])
        self.assertTrue(any(p["grupo"] == "tecnicas" for p in out["pendencias"]))
        self.assertIn("NÃO foram salvos", out["aviso"])

    def test_sem_nenhuma_fala_nada_entra(self):
        out = salvar(equipamentos={"forno": True})
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual(out["perfil"]["equipamentos"], {})
        self.assertIn("ainda não tenho a fala dela", out["recusados"][0]["motivo"])

    def test_resposta_curta_conta_com_a_pergunta(self):
        falas.registrar(SESSAO, [{"role": "assistant", "content": "Você tem forno funcionando?"}, {"role": "user", "content": "tenho sim"}])
        self.assertTrue(salvar(equipamentos={"forno": True})["success"])
        self.assertFalse(salvar(equipamentos={"air_fryer": True})["success"])

    def test_resposta_do_clarify_que_chega_no_meio_do_turno(self):
        diz("Quero montar o cardápio")
        self.assertFalse(salvar(equipamentos={"fogao": 4})["success"])
        falas.apos_tool(tool_name="clarify", session_id=SESSAO,
                        result=clarify("Seu fogão tem quantas bocas e todas funcionam?", "4", ("2", "4", "5 ou mais")))
        out = salvar(equipamentos={"fogao": 4})
        self.assertTrue(out["success"], out)
        self.assertFalse(salvar(equipamentos={"fogao": 5})["success"])  # só o número que ela escolheu
        falas.apos_tool(tool_name="despensa_listar", session_id=SESSAO, result='{"user_response": "x"}')  # outras tools não contam
        self.assertEqual(len(falas.da_sessao(SESSAO)), 2)

    def test_numero_que_ela_nao_disse(self):
        diz("Meu fogão tem 4 bocas e tenho 2 horas por cozinhada.")
        self.assertFalse(salvar(equipamentos={"fogao": 5})["success"])
        self.assertFalse(salvar(restricoes={"tempo_por_cozinhada_min": 90})["success"])
        out = salvar(equipamentos={"fogao": 4}, restricoes={"tempo_por_cozinhada_min": 120})
        self.assertTrue(out["success"])
        self.assertNotIn("recusados", out)

    def test_observacao_inventada_e_recusada(self):
        diz("Tenho forno e liquidificador.")
        out = salvar(observacoes="Ela é chef formada e trabalhou em restaurante francês.")
        self.assertFalse(out["success"])
        self.assertEqual(out["perfil"]["observacoes"], [])

    def test_lista_de_equipamentos_tambem_e_conferida(self):
        diz("Tenho forno e batedeira")
        out = salvar(equipamentos=["forno", "batedeira", "airfryer"])
        self.assertEqual(sorted(out["perfil"]["equipamentos"]), ["batedeira", "forno"])
        self.assertEqual([r["chave"] for r in out["recusados"]], ["air_fryer"])

    def test_chamada_vazia_continua_valida(self):
        diz("Tenho forno")
        out = salvar()
        self.assertTrue(out["success"])
        self.assertEqual(out["salvo"], {})

    def test_conferencia_pode_ser_desligada_para_depurar(self):
        os.environ["SABOR_DA_MARIA_CONFERIR_FALAS"] = "0"
        self.addCleanup(os.environ.pop, "SABOR_DA_MARIA_CONFERIR_FALAS", None)
        out = salvar(tecnicas={"molho branco": "sim"})
        self.assertTrue(out["success"])
        self.assertEqual(out["perfil"]["tecnicas"], {"molho_branco": "sim"})

    def test_contexto_do_hermes_e_entregue_so_a_quem_pede(self):
        # O dispatcher manda session_id, task_id, user_task...: a tool que não os declara não pode quebrar.
        out = json.loads(tools.HANDLERS["despensa_listar"]({}, session_id=SESSAO, task_id="t", user_task="x", conversation_id="c"))
        self.assertTrue(out["success"])
        diz("Tenho forno")
        out = json.loads(tools.HANDLERS["perfil_cozinha_salvar"]({"equipamentos": {"forno": True}}, session_id=SESSAO, task_id="t", user_task="x"))
        self.assertTrue(out["success"])


class HooksTests(unittest.TestCase):
    def setUp(self):
        falas.esquecer_tudo()
        self.addCleanup(falas.esquecer_tudo)

    def test_pre_llm_call_refaz_as_falas_a_cada_turno(self):
        falas.ao_iniciar_turno(session_id="s1", user_message="Tenho forno", conversation_history=[{"role": "user", "content": "Tenho forno"}], model="x")
        self.assertEqual([f.texto for f in falas.da_sessao("s1")], ["Tenho forno"])
        self.assertIsNone(falas.da_sessao("outra"))
        historico = [{"role": "user", "content": "Tenho forno"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "E liquidificador"}]
        falas.ao_iniciar_turno(session_id="s1", user_message="E liquidificador", conversation_history=historico)
        self.assertEqual(len(falas.da_sessao("s1")), 2)

    def test_hook_nunca_derruba_o_turno(self):
        falas.ao_iniciar_turno(session_id="s1", user_message=object(), conversation_history="lixo")
        falas.apos_tool(tool_name="clarify", session_id="s1", result=object())

    def test_memoria_limitada(self):
        for i in range(falas.MAX_SESSOES + 4):
            falas.registrar(f"s{i}", [{"role": "user", "content": "oi"}])
        self.assertIsNone(falas.da_sessao("s0"))
        self.assertIsNotNone(falas.da_sessao(f"s{falas.MAX_SESSOES + 3}"))

    def test_plugin_registra_os_hooks(self):
        class Contexto:
            def __init__(self):
                self.hooks, self.tools = [], []

            def register_tool(self, **kwargs):
                self.tools.append(kwargs["name"])

            def register_hook(self, nome, callback):
                self.hooks.append((nome, callback))

            def register_web_search_provider(self, provider):
                pass

        ctx = Contexto()
        log = logging.getLogger("sabor_da_maria")
        nivel = log.level
        log.setLevel(logging.ERROR)  # fora do Hermes o provider de extração não carrega: aviso esperado, sem ruído no teste
        self.addCleanup(log.setLevel, nivel)
        sabor_da_maria.register(ctx)
        self.assertEqual([n for n, _ in ctx.hooks], ["pre_llm_call", "pre_llm_call", "post_tool_call"])
        self.assertEqual(len(ctx.tools), 18)
        self.assertEqual(set(ctx.tools), set(tools.SCHEMAS))


if __name__ == "__main__":
    unittest.main()
