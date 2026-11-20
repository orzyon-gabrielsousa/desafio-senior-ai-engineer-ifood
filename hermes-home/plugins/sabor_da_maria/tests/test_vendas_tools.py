"""A LLM não pode converter planejamento ou citações em vendas realizadas."""

from datetime import datetime, timedelta, timezone
import json

from sabor_da_maria import atividade, falas, montagem, tools, vendas
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class VendasToolsTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()
        self.prato = self.store.salvar_prato("Arroz da casa", None, 4, 15.18, 3.79, {})
        self.store.aceitar_prato(self.prato, 8.5, [])

    def fala(self, texto, *, sessao=SESSAO, turno="venda-1", historico=None):
        montagem.iniciar_turno(sessao, turno)
        falas.registrar(sessao, historico or [], texto)

    def call(self, quantidade=3, *, sessao=SESSAO, turno="venda-1", **extras):
        return json.loads(tools.vendas_registrar({"prato_id": self.prato, "quantidade": quantidade, **extras},
                                                session_id=sessao, turn_id=turno))

    def quantidade(self):
        return vendas.resumo(self.store)["periodos"]["dia"]["quantidade"]

    def test_fato_real_nome_ou_id_precos_literal_e_comprovante_conferido(self):
        self.fala("Hoje vendi 3 porções de Arroz da casa a R$ 9,00 cada. Pode registrar?")
        resultado = self.call(preco_unitario=9)
        self.assertTrue(resultado["success"], resultado)
        self.assertEqual(resultado["venda"]["faturamento_bruto"], 27)
        self.assertEqual(resultado["venda"]["preco_unitario"], 9)
        self.assertIn("R$ 27,00", resultado["diga_a_dona_maria"])
        self.assertIn("não altera o orçamento", resultado["diga_a_dona_maria"])
        pronta = montagem.resposta_preparada(SESSAO, "venda-1", prioridade_minima=50)
        self.assertEqual(pronta, resultado["diga_a_dona_maria"])
        self.assertEqual(self.store.obter_prato(self.prato)["preco_venda"], 8.5)
        self.assertEqual(self.store.carrinho_resumo()["saldo"], 80)

    def test_planejamento_simulacao_negacao_e_citacao_nao_sao_vendas(self):
        textos = [f"Quero vender 3 porções do prato #{self.prato}.", f"Se eu vender 3 porções do prato #{self.prato}.",
                  f"Simule: vendi 3 porções do prato #{self.prato}.", f"Não vendi 3 porções do prato #{self.prato}.",
                  f"Acho que vendi 3 porções do prato #{self.prato}.", f'Copiei: "Vendi 3 porções do prato #{self.prato}."',
                  f"`Vendi 3 porções do prato #{self.prato}.`", f"> Vendi 3 porções do prato #{self.prato}.",
                  f"Vendi 3 ou 4 porções do prato #{self.prato}.", f"Vendi 3/4 porções do prato #{self.prato}.",
                  f"Meu vizinho vendeu 3 porções do prato #{self.prato}.", "sim"]
        for i, texto in enumerate(textos):
            with self.subTest(texto=texto):
                self.fala(texto, turno=f"falso-{i}")
                self.assertFalse(self.call(turno=f"falso-{i}")["success"])
        self.assertEqual(self.quantidade(), 0)

    def test_numero_do_prato_nao_e_quantidade_e_quantidade_nao_pode_ser_inventada(self):
        self.fala(f"Vendi o prato #{self.prato}.")
        self.assertFalse(self.call(quantidade=self.prato)["success"])
        self.fala(f"Vendi 3 porções do prato #{self.prato}.")
        self.assertFalse(self.call(quantidade=10)["success"])
        self.assertFalse(self.call(quantidade=True)["success"])
        self.assertEqual(self.quantidade(), 0)

    def test_venda_propria_nao_autoriza_registrar_a_de_um_vizinho_em_outro_trecho(self):
        segundo = self.store.salvar_prato("Frango da casa", None, 4, 12, 3, {})
        self.store.aceitar_prato(segundo, 10, [])
        for separador in (". ", "; ", "\n"):
            with self.subTest(separador=separador):
                self.fala(f"Vendi 3 porções do prato #{self.prato}{separador}Meu vizinho vendeu 2 porções do prato #{segundo}.")
                resultado = json.loads(tools.vendas_registrar({"prato_id": segundo, "quantidade": 2}, session_id=SESSAO))
                self.assertFalse(resultado["success"], resultado)
        self.assertEqual(self.quantidade(), 0)
        self.assertTrue(self.call()["success"])
        self.assertEqual(self.quantidade(), 3)

    def test_pedido_para_nao_registrar_impede_gravacao_mesmo_com_fato_real(self):
        for pedido in ("Não registre ainda.", "Não quero que registre.", "Não salve essa venda.", "Ainda não pode lançar."):
            with self.subTest(pedido=pedido):
                self.fala(f"Vendi 3 porções do prato #{self.prato}. {pedido}")
                resultado = self.call()
                self.assertFalse(resultado["success"], resultado)
                self.assertIn("não registrar", resultado["diga_a_dona_maria"])
        self.assertEqual(self.quantidade(), 0)

    def test_preco_omitido_usa_cardapio_mas_dinheiro_dito_nao_e_ignorado(self):
        self.fala(f"Vendi três marmitas do prato #{self.prato}.")
        self.assertEqual(self.call()["venda"]["preco_unitario"], 8.5)
        self.fala(f"Vendi 3 porções do prato #{self.prato} por 7 reais cada.", turno="desconto")
        self.assertEqual(self.call(turno="desconto")["venda"]["preco_unitario"], 7)
        self.fala(f"Vendi 3 porções do prato #{self.prato} a R$ 9,00 cada.", turno="valor-nao-dito")
        self.assertFalse(self.call(turno="valor-nao-dito", preco_unitario=99)["success"])
        self.fala(f"Vendi 3 porções do prato #{self.prato}, no total de R$ 30,00.", turno="total")
        self.assertFalse(self.call(turno="total", preco_unitario=30)["success"])

    def test_preco_por_porcao_sem_moeda_e_ponto_final_sao_preservados(self):
        for i, (frase, preco, explicito) in enumerate((
                ("a 9,00 cada.", 9, False), ("a 9,00 cada.", 9, True),
                ("por 7,50 por porção.", 7.5, False), ("a R$ 27,00. Obrigada.", 27, False))):
            with self.subTest(frase=frase, explicito=explicito):
                turno = f"preco-literal-{i}"
                self.fala(f"Vendi 3 porções do prato #{self.prato} {frase}", turno=turno)
                resultado = self.call(turno=turno, **({"preco_unitario": preco} if explicito else {}))
                self.assertTrue(resultado["success"], resultado)
                self.assertEqual(resultado["venda"]["preco_unitario"], preco)
        self.fala(f"Vendi 3 porções do prato #{self.prato} a nove cada.", turno="preco-extenso")
        self.assertFalse(self.call(turno="preco-extenso")["success"])

    def test_retentativa_mesmo_turno_e_nova_venda_em_outro_turno(self):
        texto = f"Vendi 3 porções do prato #{self.prato}."
        self.fala(texto)
        primeira = self.call()
        self.assertFalse(primeira["ja_registrada"])
        repetida = self.call()
        self.assertTrue(repetida["ja_registrada"])
        self.assertEqual(primeira["venda"]["id"], repetida["venda"]["id"])
        self.assertEqual(self.quantidade(), 3)
        self.fala(texto, turno="venda-2")
        outra = self.call(turno="venda-2")
        self.assertFalse(outra["ja_registrada"])
        self.assertNotEqual(primeira["venda"]["operacao"], outra["venda"]["operacao"])
        self.assertEqual(self.quantidade(), 6)

    def test_contexto_atual_exigido_sem_reaproveitar_fala_de_outro_chat(self):
        texto = f"Vendi 3 porções do prato #{self.prato}."
        self.fala(texto)
        self.assertFalse(self.call(sessao="outra-sessao")["success"])
        self.assertFalse(self.call(turno="turno-antigo")["success"])
        self.assertFalse(self.call(sessao="")["success"])
        falas.registrar("sem-turno", [], texto)
        self.assertFalse(self.call(sessao="sem-turno")["success"])
        self.fala("Qual é o saldo?", historico=[{"role": "user", "content": texto}, {"role": "assistant", "content": "Entendi."}])
        self.assertFalse(self.call()["success"])
        self.assertEqual(self.quantidade(), 0)

    def test_data_antiga_exige_iso_literal_sem_reinterpretar_ou_usar_hoje(self):
        self.fala(f"Ontem vendi 3 porções do prato #{self.prato}.")
        self.assertFalse(self.call()["success"])
        data = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        self.fala(f"Vendi 3 porções do prato #{self.prato} em {data}.")
        self.assertFalse(self.call(vendido_em="2026-01-01T00:00:00-03:00")["success"])
        resultado = self.call(vendido_em=data)
        self.assertTrue(resultado["success"], resultado)
        self.assertEqual(resultado["venda"]["vendido_em"], data)
        self.assertEqual(self.quantidade(), 0)

    def test_datas_naturais_nao_viram_hoje_e_data_futura_contraditoria_nao_e_registrada(self):
        for i, data in enumerate(("na segunda-feira", "na segunda feira", "na segunda", "dia 18 de novembro",
                                  "dia dezoito de novembro", "em 18 de novembro", "amanhã", "no próximo mês")):
            with self.subTest(data=data):
                turno = f"data-natural-{i}"
                self.fala(f"Vendi 3 porções do prato #{self.prato} {data}.", turno=turno)
                resultado = self.call(turno=turno)
                self.assertFalse(resultado["success"], resultado)
                self.assertIn("data", resultado["diga_a_dona_maria"])
        passada = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        self.fala(f"Vendi 3 porções do prato #{self.prato} amanhã, em {passada}.")
        self.assertFalse(self.call(vendido_em=passada)["success"])
        self.assertEqual(self.quantidade(), 0)

    def test_multiplos_pratos_mesmo_turno_chaves_distintas_quantidades_vinculadas(self):
        segundo = self.store.salvar_prato("Frango da casa", None, 4, 12, 3, {})
        self.store.aceitar_prato(segundo, 10, [])
        self.fala(f"Vendi 3 porções do prato #{self.prato} e 2 porções do prato #{segundo}.")
        primeiro = self.call()
        outro = json.loads(tools.vendas_registrar({"prato_id": segundo, "quantidade": 2}, session_id=SESSAO))
        self.assertTrue(primeiro["success"], primeiro)
        self.assertTrue(outro["success"], outro)
        self.assertNotEqual(primeiro["venda"]["operacao"], outro["venda"]["operacao"])
        self.assertEqual(self.quantidade(), 5)

    def test_consulta_e_atividade_so_expoem_fatos_registrados(self):
        sem_venda = json.loads(tools.vendas_consultar({"periodo": "mes"}))
        self.assertEqual(sem_venda["resumo"]["faturamento_bruto"], 0)
        self.fala(f"Dona Maria vendeu 3 porções do prato #{self.prato}.")
        resultado = self.call()
        self.assertTrue(resultado["success"], resultado)
        consulta = json.loads(tools.vendas_consultar({"periodo": "mes"}))
        self.assertEqual(consulta["resumo"]["quantidade"], 3)
        self.assertEqual(consulta["resumo"]["faturamento_bruto"], 25.5)
        descritivo = atividade.descrever("vendas_registrar", {}, resultado)
        self.assertTrue(descritivo["ok"])
        self.assertIn("3 porções", descritivo["resumo"])
        self.assertIn("R$ 25,50", descritivo["resumo"])
