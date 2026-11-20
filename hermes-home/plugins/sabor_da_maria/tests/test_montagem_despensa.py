"""Montar um prato nomeado a partir da despensa ("pizza de mussarela com o que tenho").

Cobre o pedido que antes travava: a consultora perguntava "quais ingredientes?" em vez
de montar a ficha local, e o modo prato próprio nem chamava a montagem.
"""

import json

from sabor_da_maria import cozinha, falas, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase

PIZZA = "quero montar uma pizza de mussarela com os ingredientes que tenho"
PEDIDO = ("Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. "
          "Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.")
URL_WEB = "https://www.tudogostoso.com.br/receita/pizza-de-frango-com-mussarela.html"
NOME_WEB = "Pizza de frango com mussarela"


class MontagemDaDespensaTests(StoreTestCase):
    def _cozinha(self, *, massa_fermentada="sim"):
        store = Store()
        store.cozinha_salvar({
            "equipamentos": {"fogao": 6, "forno": True, "forma": True, "panela_grande": True},
            "habilidades": {"assar": "sim", "molhos": "sim", "refogar": "sim", "massa_fermentada": massa_fermentada},
            "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"},
        })
        return store

    def test_reconhece_pedido_nomeado_da_despensa_e_exclusao(self):
        for texto in (PIZZA, "quero montar uma pizza de mussarela com o que tenho",
                      "monta uma pizza de queijo usando os ingredientes que tenho",
                      "monte um prato com os ingredientes que tenho", "então monta esse prato sem azeitona",
                      "monta esse prato sem a azeitona", "quero montar uma pizza sem azeitona"):
            self.assertTrue(montagem.pediu_montagem(texto), texto)
        # Um pedido genérico de descoberta continua indo para a pesquisa web, não para a ficha local.
        self.assertTrue(montagem.pediu_montagem("monte um prato para meu cardápio"))
        self.assertFalse(montagem.pediu_montagem("Adapte o preço para cobrir os custos."))
        self.assertFalse(montagem.pediu_montagem("Qual é o saldo?"))

    def test_monta_a_pizza_da_despensa_sem_compras_nem_azeitona(self):
        store = self._cozinha()
        montagem.iniciar_turno(SESSAO, "pizza")
        conversa = falas.extrair([], PIZZA)
        contexto = montagem.propor_turno(store, conversa, SESSAO)
        self.assertIn("Pizza de mussarela", contexto)
        self.assertIn("Compras complementares: R$ 0,00", contexto)
        self.assertIn("sem azeitona e sem orégano", contexto)
        self.assertIn("aceita preparar o prato", contexto)
        prato, = store.pratos_em_andamento()
        self.assertEqual((prato["status"], prato["veredito"], prato["porcoes"]), ("selecionado", "viavel", 4))
        self.assertEqual(prato["compras_estimadas"], 0)
        receita = store.obter_receita(prato["receita_id"])
        nomes = [i["nome"] for i in receita["dados"]["ingredientes"]]
        self.assertNotIn("Azeitona verde", nomes)
        self.assertTrue(all(store.obter(nome) for nome in nomes))
        # A proposta ainda não compromete estoque nem saldo.
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.orcamento()["saldo"], 80)

    def test_pizza_sem_cozinha_confirmada_pergunta_so_o_requisito_que_falta(self):
        store = self._cozinha(massa_fermentada=None)
        conversa = falas.extrair([], PIZZA)
        contexto = montagem.propor_turno(store, conversa, SESSAO)
        self.assertIn("Pizza de mussarela", contexto)
        self.assertIn("massas com fermento", contexto)
        prato, = store.pratos_em_andamento()
        self.assertEqual(prato["veredito"], "precisa_confirmar")
        self.assertEqual(store.reservas_ativas(), [])

    def test_exclusao_sem_ingrediente_remove_o_item_da_ficha(self):
        store = self._cozinha()
        conversa = falas.extrair([], "quero montar uma pizza sem mussarela")
        contexto = montagem.propor_turno(store, conversa, SESSAO)
        self.assertIn("Pizza de mussarela", contexto)
        self.assertIn("Retirei mussarela", contexto)
        prato, = store.pratos_em_andamento()
        receita = store.obter_receita(prato["receita_id"])
        self.assertFalse(any("mussarela" in i["nome"].lower() for i in receita["dados"]["ingredientes"]))

    def test_aceite_da_pizza_calcula_cmv_reserva_estoque_e_salva_cenarios(self):
        store = self._cozinha()
        montagem.iniciar_turno(SESSAO, "pizza")
        contexto = montagem.propor_turno(store, falas.extrair([], PIZZA), SESSAO)
        prato, = store.pratos_em_andamento()
        falas.registrar(SESSAO, [{"role": "assistant", "content": contexto}],
                        "Sim, aceito preparar essa pizza de mussarela.")
        cmv = json.loads(tools.prato_calcular_cmv({"prato_id": prato["id"]}, session_id=SESSAO))
        self.assertTrue(cmv["success"], cmv)
        self.assertGreater(cmv["cmv_por_porcao"], 0)
        self.assertEqual(cmv["compras_total_estimado"], 0)
        analise = json.loads(tools.preco_sugerir({"prato_id": prato["id"]}, session_id=SESSAO))
        self.assertTrue(analise["analise_salva"], analise)
        self.assertTrue(analise["carrinho_atualizado"])
        self.assertEqual({r["prato_id"] for r in store.reservas_ativas()}, {prato["id"]})
        # Só despensa: nenhum complemento novo, o saldo de compras não muda.
        self.assertEqual(store.orcamento()["saldo"], 80)
        self.assertEqual(len(store.analises_preco()), 1)

    def test_no_modo_proprio_o_pedido_da_despensa_monta_em_vez_de_perguntar(self):
        store = self._cozinha()
        store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "prato_proprio")
        self.assertTrue(jornada.resumo(store)["controle"]["proprio_pendente"])
        historico = [{"role": "assistant", "content": "Qual prato ou ingrediente você tem em mente para criar seu próprio prato?"}]
        retorno = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="proprio-pizza",
                                          conversation_history=historico, user_message=PIZZA)
        self.assertIn("Pizza de mussarela", retorno["context"])
        self.assertIn("aceita preparar o prato", retorno["context"])
        prato, = store.pratos_em_andamento()
        self.assertEqual((prato["nome"], prato["status"], prato["veredito"]), ("Pizza de mussarela", "selecionado", "viavel"))
        self.assertFalse(jornada.resumo(store)["controle"]["proprio_pendente"])
        self.assertEqual(store.reservas_ativas(), [])


class OfertaDaDespensaNaEscolhaTests(StoreTestCase):
    """Complementos sem preço na receita web escolhida: a consultora oferece a versão da despensa."""

    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.store.cozinha_salvar({
            "equipamentos": {i.id: (6 if i.id == "fogao" else True) for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE},
            "habilidades": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
            "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"}})

    def _turno(self, mensagem, turno, *respostas):
        historico = []
        for i, resposta in enumerate(respostas):
            historico += [{"role": "user", "content": PEDIDO if i == 0 else mensagem}, {"role": "assistant", "content": resposta}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=mensagem)
        return painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, conversation_history=historico, user_message=mensagem)["context"]

    def _ferramenta(self, nome, dados):
        return json.loads(pesquisa.depois_da_ferramenta(tool_name=nome, result=json.dumps(dados), session_id=SESSAO))

    def _escolher_pizza_web(self):
        receita = {"url": URL_WEB, "title": NOME_WEB, "metadata": {"recipe_schema": True}, "receitas": [{
            "nome": NOME_WEB, "url": URL_WEB, "porcoes": 4, "tempo_preparo_min": 40,
            "ingredientes": ["8 unidades médias de azeitona verde", "1 unidade de massa para pizza Massa Leve",
                             "2 colheres (arroz) cheias de molho de tomate caseiro", "10 g de orégano seco Masterfoods",
                             "400 g de peito de frango desfiado temperado", "200 g de queijo muçarela"],
            "modo_preparo": "Abra a massa, espalhe o molho, distribua o frango e a mussarela e asse em forno quente."}]}
        self._turno(PEDIDO, "sugestoes")
        self._ferramenta("despensa_listar", {"success": True})
        self._ferramenta("web_search", {"success": True, "data": {"web": [{"title": NOME_WEB, "url": URL_WEB}]}})
        self._ferramenta("web_extract", {"results": [receita]})
        self._ferramenta("despensa_buscar", {"success": True, "resultados": []})
        lista = montagem.resposta_preparada(SESSAO, "sugestoes")
        self._turno(f"Gostei de «{NOME_WEB}», vamos usá-lo.", "gostei", lista)
        return json.loads(tools.receita_conferir_ingredientes({}, session_id=SESSAO))

    def test_complemento_sem_preco_preserva_prato_escolhido_com_faltantes(self):
        ficha = self._escolher_pizza_web()
        self.assertTrue(any(i.get("falta_preco") for i in ficha["ingredientes"]))
        self.assertNotIn("proposta_equivalente", ficha)
        original, = self.store.pratos_em_andamento()
        self.assertEqual((original["nome"], original["status"]), (NOME_WEB, "selecionado"))
        self.assertTrue(original["ingredientes_faltantes"])
        resposta = montagem.resposta_preparada(SESSAO, "gostei")
        self.assertIn(NOME_WEB, resposta)
        self.assertIn("Quer que eu pesquise o preço", resposta)
        self.assertNotIn("Pizza de mussarela", resposta)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_aceite_da_versao_oferecida_reserva_estoque_e_salva_cenarios(self):
        ficha = self._escolher_pizza_web()
        pergunta = jornada.resumo(self.store)["pergunta"]
        adaptar = pergunta["opcoes"][1]["mensagem"]
        contexto = self._turno(adaptar, "adaptar", montagem.resposta_preparada(SESSAO, "gostei"))
        self.assertIn("ADAPTAÇÃO DELEGADA", contexto)
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "adaptar"))
        adaptada = json.loads(tools.receita_conferir_ingredientes({
            "prato_id": ficha["prato_id"], "porcoes": 4, "tempo_preparo_min": 60,
            "adaptacao": "Pizza de massa fina sem fermentação, sem massa comprada, azeitona nem orégano.",
            "modo_preparo": "Misture farinha e água, abra massa fina. Cozinhe o frango completamente, desfie, cubra a massa com frango e mussarela e asse no forno.",
            "ingredientes": [{"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"},
                             {"nome": "Água", "quantidade": 250, "unidade": "ml"},
                             {"nome": "Peito de frango", "quantidade": 300, "unidade": "g"},
                             {"nome": "Queijo mussarela", "quantidade": 200, "unidade": "g"}]}, session_id=SESSAO))
        self.assertEqual(adaptada["veredito"], "viavel", adaptada)
        local = self.store.obter_prato(ficha["prato_id"])
        self.assertEqual(local["id"], ficha["prato_id"])
        resposta = montagem.resposta_preparada(SESSAO, "adaptar")
        falas.registrar(SESSAO, [{"role": "assistant", "content": resposta}], f"Sim, aceito preparar o prato #{local['id']} nessa versão de massa fina.")
        cmv = json.loads(tools.prato_calcular_cmv({"prato_id": local["id"]}, session_id=SESSAO))
        self.assertTrue(cmv["success"], cmv)
        analise = json.loads(tools.preco_sugerir({"prato_id": local["id"]}, session_id=SESSAO))
        self.assertTrue(analise["analise_salva"], analise)
        self.assertEqual({r["prato_id"] for r in self.store.reservas_ativas()}, {local["id"]})
        self.assertEqual(self.store.orcamento()["saldo"], 80)
