"""Receber sugestões não autoriza criar fichas."""

import json

from sabor_da_maria import jornada, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class SugestoesSemGravacaoTests(StoreTestCase):
    def test_pedido_do_botao_de_ajuda_bloqueia_gravacoes_ate_a_escolha(self):
        store = Store()
        store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(store, "confirmar_estoque")
        ajuda = next(opcao for opcao in jornada.resumo(store)["pergunta"]["opcoes"] if opcao["acao"] == "ajuda_ia")
        jornada.responder(store, ajuda["acao"])
        painel.ao_iniciar_turno(user_message=ajuda["mensagem"], session_id=SESSAO, turn_id="sugestoes")
        self.assertTrue(pesquisa.somente_sugestoes(SESSAO))

        for ferramenta, argumentos in (
            (tools.pratos_selecionar, {"nomes": ["Frango com batata"]}),
            (tools.receita_conferir_ingredientes, {"nome": "Frango com batata", "ingredientes": [
                {"nome": "Peito de frango", "quantidade": 500, "unidade": "g"},
            ]}),
        ):
            self.assertIn("aguarde a escolha", json.loads(ferramenta(argumentos, session_id=SESSAO))["error"])

        self.assertEqual(store.pratos(), [])
        self.assertEqual(store.receitas(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)

        painel.ao_iniciar_turno(session_id=SESSAO, turn_id="escolha", user_message="Escolho 1", conversation_history=[{
            "role": "assistant", "content": "### 1. Frango com batata\nFonte: [Frango com batata](https://exemplo.com/frango)\n"
                                           "Já tem: frango e batata.\nQual opção você quer guardar?",
        }])
        self.assertFalse(pesquisa.somente_sugestoes(SESSAO))
        self.assertEqual([prato["nome"] for prato in store.pratos()], ["Frango com batata"])
