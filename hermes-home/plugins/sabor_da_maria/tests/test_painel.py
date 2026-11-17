"""Painel da tela: a consultora sabe o carrinho, o saldo e o negócio sem precisar chamar tool."""

import logging
import os

from sabor_da_maria import falas, painel
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class PainelTests(StoreTestCase):
    def test_painel_vazio(self):
        texto = painel.texto_do_painel()
        self.assertTrue(texto.startswith(falas.MARCA_PAINEL))
        self.assertIn("Saldo: orçamento R$ 80,00 · no carrinho R$ 0,00 · saldo R$ 80,00.", texto)
        self.assertIn("Carrinho: vazio.", texto)
        self.assertIn("ainda não preencheu o perfil do negócio", texto)
        self.assertTrue(texto.rstrip().endswith("[fim do painel]"))

    def test_painel_com_itens_e_negocio(self):
        store = Store()
        store.carrinho_adicionar("Couve", 12, 1, "maço", estimado=True)
        store.carrinho_adicionar("Alho", 3)
        store.negocio_salvar({"tipo": "restaurante_hotel", "alimentacao": ["caseira", "doces"], "estado": "SP", "cidade": "São Paulo", "zona": "sul"})
        texto = painel.texto_do_painel(store)
        self.assertIn("no carrinho R$ 15,00 · saldo R$ 65,00", texto)
        self.assertIn("Couve (1 maço, R$ 12,00, preço estimado) | Alho (1, R$ 3,00)", texto)
        self.assertIn("Restaurante de hotel", texto)
        self.assertIn("faz: Comida caseira e prato do dia; Doces e sobremesas", texto)
        self.assertIn("local: Zona Sul, São Paulo/SP", texto)

    def test_painel_mostra_o_que_ja_esta_comprometido(self):
        store = Store()
        store.carrinho_adicionar("Couve", 12)
        # compromisso de prato aceito aparece à parte do carrinho
        prato = store.salvar_prato("Prato", None, 2, 5.0, 2.5, {"ingredientes": []})
        store.aceitar_prato(prato, 20.0, [{"ingrediente": "creme de leite", "quantidade": 200, "unidade": "g", "preco_estimado": 3.5}])
        texto = painel.texto_do_painel(Store())
        self.assertIn("reservado para compras de pratos R$ 3,50", texto)
        self.assertIn("saldo R$ 64,50", texto)

    def test_lista_longa_e_cortada(self):
        store = Store()
        for i in range(painel.MAX_ITENS + 2):
            store.carrinho_adicionar(f"Item {i}", 1)
        texto = painel.texto_do_painel(store)
        self.assertIn("e mais 2 itens", texto)
        self.assertNotIn(f"Item {painel.MAX_ITENS + 1} (", texto)

    def test_hook_devolve_o_painel_como_contexto(self):
        resposta = painel.ao_iniciar_turno(session_id="s", user_message="oi", conversation_history=[])
        self.assertEqual(list(resposta), ["context"])
        self.assertIn("saldo R$ 80,00", resposta["context"])

    def test_chat_usa_convite_clicavel_e_avanca_apos_salvar(self):
        resposta = painel.ao_iniciar_turno(
            is_first_turn=True, session_id="s",
            user_message="Oi!\n\n[painel da tela: cadastro guiado no chat]",
        )["context"]
        self.assertIn("[[cadastro:negocio]]", resposta)
        self.assertNotIn("PERGUNTAS PARA FAZER", resposta)
        Store().negocio_salvar({"tipo": "restaurante_hotel", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo"})
        resposta = painel.ao_iniciar_turno(
            session_id="s",
            user_message="Enviei meu cadastro do negócio pelo chat.\n\n[painel da tela: cadastro guiado no chat; salvou negocio]",
        )["context"]
        self.assertIn("O formulário do negócio já gravou", resposta)
        self.assertIn("[[cadastro:cozinha]]", resposta)
        self.assertNotIn("[[cadastro:negocio]]", resposta)

    def test_hook_nunca_derruba_o_turno(self):
        log = logging.getLogger("sabor_da_maria.painel")
        nivel = log.level
        log.setLevel(logging.CRITICAL)
        self.addCleanup(log.setLevel, nivel)
        os.environ["SABOR_DA_MARIA_DB"] = "/proc/nao-existe/sabor.db"  # StoreTestCase restaura no tearDown
        self.assertIsNone(painel.ao_iniciar_turno())

    def test_o_painel_nao_vira_evidencia_do_perfil(self):
        Store().carrinho_adicionar("Faca de cozinha", 9)
        mensagem = "Tenho forno\n\n" + painel.texto_do_painel()
        lista = falas.extrair([{"role": "user", "content": mensagem}])
        self.assertEqual([f.texto for f in lista], ["Tenho forno"])
        self.assertTrue(falas.apoia(["forno"], lista))
        self.assertFalse(falas.apoia(["faca"], lista))
        em_partes = falas.extrair([{"role": "user", "content": [{"type": "text", "text": mensagem}]}])
        self.assertEqual([f.texto for f in em_partes], ["Tenho forno"])
