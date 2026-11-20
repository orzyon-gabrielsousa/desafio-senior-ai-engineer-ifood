"""O fluxo normal do chat deve explicar os números que a usuária vai aprovar."""

import json

from sabor_da_maria import falas, montagem, painel, tools
from sabor_da_maria.store import Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args, session_id=SESSAO))


class NarrativaDoDesafioTests(StoreTestCase):
    def test_aceite_mostra_custo_repasse_lucro_e_aguarda_preco(self):
        diz(FALA_COZINHA)
        call("perfil_cozinha_salvar",
             equipamentos={"fogão": 4, "forno": True, "panela de pressão": True,
                           "air fryer": False, "liquidificador": True},
             restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120,
                         "gas": "botijão", "energia": "estável"},
             tecnicas={"refogado": "sim"})
        receita = call("receita_conferir_ingredientes", nome="Frango em cubos", porcoes=4,
                       tempo_preparo_min=30,
                       ingredientes=[{"nome": "peito de frango", "quantidade": 500, "unidade": "g"}])
        cmv = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        prato_id = cmv["prato_id"]
        historico = [{"role": "assistant", "content":
                      f"Você aceita preparar o prato #{prato_id} Frango em cubos nessa versão?"}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message="Sim, aceito.")
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="aceite-matematica",
                                          conversation_history=historico, user_message="Sim, aceito.")
        self.assertIn("análise foi salva", contexto["context"])
        texto = montagem.completar_resposta("Resumo sem contas", SESSAO, "aceite-matematica")
        for conta in ("R$ 28,00 ÷ 2 kg = R$ 14,00/kg", "500 g × R$ 14,00/kg = R$ 7,00",
                      "R$ 7,00** ÷ 4 porções", "R$ 1,75 ÷ 0,90", "R$ 1,95", "10%",
                      "Recebe 0,90 ×", "menos CMV R$ 1,75", "lucro", "Qual preço por porção"):
            self.assertIn(conta, texto)
        store = Store()
        self.assertIsNone(store.obter_prato(prato_id)["preco_venda"])
        self.assertEqual([r["quantidade"] for r in store.reservas_do_prato(prato_id)], ["500 g"])
        self.assertTrue(store.carrinho_itens()[0]["ja_tem"])
        analise, = store.analises_preco()
        self.assertIn(len(analise["dados"]["cenarios"]), (2, 3))
