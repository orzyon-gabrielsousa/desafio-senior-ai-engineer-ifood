"""Escolher receitas depois do modo próprio não funde nem descarta as fichas."""

from sabor_da_maria import escolhas, falas, jornada, painel, pesquisa
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


LISTA = """1. Marmita de Frango com Quiabo Frito
Fonte: [Marmita de Frango com Quiabo Frito](https://example.org/marmita-quiabo)
Já tem: peito de frango, cebola, alho e arroz.
Precisa conferir ou comprar: quiabo e limão.

2. Frango com Quiabo (Panelinha)
Fonte: Frango com quiabo - Panelinha
Já tem: peito de frango e cebola.
Precisa conferir ou comprar: quiabo, limão e cominho.

Você gosta de cozinhar uma dessas opções? Vê algum impedimento?
"""
PEDIDO = "quero esses dois 2. Frango com Quiabo (Panelinha) 1. Marmita de Frango com Quiabo Frito"
PERGUNTA = "Você escolheu criar seu próprio prato. Poderia me dizer qual prato ou ingrediente tem em mente?"


class EscolhaDepoisDoModoProprioTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")

    def test_dois_titulos_com_numeros_sao_duas_escolhas_conferidas(self):
        opcoes = escolhas.escolhidas(PEDIDO, LISTA)
        self.assertEqual([o["numero"] for o in opcoes], [1, 2])
        self.assertEqual([o["nome"] for o in opcoes], ["Marmita de Frango com Quiabo Frito", "Frango com Quiabo (Panelinha)"])
        self.assertIsNone(opcoes[1]["fonte_url"])
        for pedido in ("quero esses três 2. Frango com Quiabo (Panelinha) 1. Marmita de Frango com Quiabo Frito",
                       "quero esses dois 2. Frango com Quiabo (Panelinha)",
                       "não quero esses dois 2. Frango com Quiabo (Panelinha) 1. Marmita de Frango com Quiabo Frito",
                       "quero esses dois 2. Frango com Quiabo (Panelinha) 1. Marmita de Frango com Quiabo Frito?"):
            self.assertEqual(escolhas.escolhidas(pedido, LISTA), [], pedido)

    def test_escolha_integral_aceita_so_a_selecao_sem_alterar_a_fonte(self):
        for texto in (PEDIDO, "Quero os dois.", "Sim, pode guardar as duas opções.", "Vou querer ambas.", "escolho 1 e 2", "Escolho 2.",
                      "1. Marmita de Frango com Quiabo Frito", "Frango com Quiabo (Panelinha)",
                      "Quero esses 2: 2. Frango com Quiabo (Panelinha); 1. Marmita de Frango com Quiabo Frito.",
                      "Quero guardar Marmita de Frango com Quiabo Frito e Frango com Quiabo (Panelinha) para mim."):
            self.assertTrue(escolhas.escolha_integral(texto, LISTA), texto)
        for texto in (PEDIDO + " sem limão", PEDIDO + " mas sem limão", PEDIDO + " troque o quiabo por abobrinha",
                      PEDIDO + " para mais porções", PEDIDO + " com 10 porções", PEDIDO + " 10", PEDIDO + " 2",
                      PEDIDO + " 0,5 kg", PEDIDO + " com menos sal", PEDIDO + " para vender a R$ 15,00",
                      PEDIDO + " para eu ter lucro", PEDIDO + " não compre nada", PEDIDO + " confirme o gás",
                      "Continue", "Sim, gostei e não vejo impedimento", "Não quero os dois.",
                      "Quero os dois?", 'Copiei: "Quero os dois"', "Quero os dois no carrinho"):
            self.assertFalse(escolhas.escolha_integral(texto, LISTA), texto)

    def test_hook_salva_as_duas_apos_pergunta_propria_e_repeticao_nao_duplica(self):
        jornada.responder(self.store, "prato_proprio")
        historico = [{"role": "assistant", "content": LISTA},
                     {"role": "user", "content": "Quero montar meu próprio prato."},
                     {"role": "assistant", "content": PERGUNTA}]
        estoque, saldo, perfil = self.store.ingredientes(), self.store.orcamento(), self.store.perfil()
        for turno in ("primeira-escolha", "retentativa"):
            falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=PEDIDO)
            retorno = painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno,
                                              conversation_history=historico, user_message=PEDIDO)
            self.assertIn("Escolhas gravadas", retorno["context"])
            self.assertIn("São fichas separadas", retorno["context"])
            self.assertIn("Comece a montar a ficha do prato #1", retorno["context"])
            self.assertNotIn("Confirme qual ficha quer revisar primeiro", retorno["context"])
            self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_extract")
            self.assertFalse(pesquisa.montando(SESSAO))
        pratos = self.store.pratos_em_andamento()
        self.assertEqual([(p["id"], p["nome"], p["status"]) for p in pratos],
                         [(1, "Marmita de Frango com Quiabo Frito", "selecionado"),
                          (2, "Frango com Quiabo (Panelinha)", "selecionado")])
        self.assertIsNone(pratos[1]["fonte_url"])
        atual = jornada.resumo(Store())
        self.assertFalse(atual["controle"]["proprio_pendente"])
        self.assertEqual(atual["pratos_ativos"], [1, 2])
        self.assertEqual(atual["prato_id"], 1)
        self.assertEqual((self.store.ingredientes(), self.store.orcamento(), self.store.perfil()), (estoque, saldo, perfil))
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vendas").fetchone()[0], 0)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id="duvida", user_message="Qual é o saldo?")
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))

    def test_selecao_explicita_reativa_ids_existentes_sem_apagar_outro_rascunho(self):
        anteriores = self.store.selecionar_pratos(escolhas.opcoes_exibidas(LISTA))
        terceiro, = self.store.selecionar_pratos([{"nome": "Outro rascunho"}])
        terceiro_antes = self.store.obter_prato(terceiro["id"])
        jornada.responder(self.store, "prato_proprio")
        self.assertTrue(jornada.resumo(self.store)["controle"]["proprio_pendente"])
        escolhas.preparar_turno(self.store, PEDIDO, LISTA)
        atual = jornada.resumo(Store())
        self.assertFalse(atual["controle"]["proprio_pendente"])
        self.assertEqual(atual["pratos_ativos"], [p["id"] for p in anteriores])
        self.assertEqual(atual["prato_id"], anteriores[0]["id"])
        self.assertEqual(len(self.store.pratos()), 3)
        self.assertEqual(self.store.obter_prato(terceiro["id"]), terceiro_antes)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_pergunta_operacional_so_oferece_fato_do_lote_e_tempo_fica_aberto(self):
        perfil = self.store.perfil()
        for tipo, termo in (("gas_a_confirmar", "gás"), ("energia_a_confirmar", "energia"), ("refrigeracao_a_confirmar", "geladeira")):
            pergunta = jornada._pergunta({"id": 7}, [{"tipo": tipo, "detalhe": "Condição a confirmar para o preparo."}])
            self.assertEqual(len(pergunta["opcoes"]), 2)
            for opcao in pergunta["opcoes"]:
                self.assertIn(termo, opcao["resposta"])
                self.assertIn("este lote", opcao["resposta"])
                self.assertIn("Continue o prato #7.", opcao["mensagem"])
        pergunta = jornada._pergunta({"id": 7}, [{"tipo": "tempo_da_receita_pendente", "detalhe": "Dado numérico a informar."}])
        self.assertEqual(pergunta["opcoes"], [])
        capacidade = jornada._pergunta({"id": 7}, [{"tipo": "fogao_capacidade_pendente", "detalhe": "Dado numérico a informar."}])
        self.assertEqual([o["rotulo"] for o in capacidade["opcoes"]], ["1 boca", "2 bocas", "4 bocas", "6 bocas"])
        for opcao in capacidade["opcoes"]:
            resposta = falas.extrair([], opcao["mensagem"])[-1]
            self.assertIsNotNone(falas.bocas_fogao_declaradas(resposta))
            self.assertIn("Continue o prato #7.", opcao["mensagem"])
        self.assertEqual(self.store.perfil(), perfil)
