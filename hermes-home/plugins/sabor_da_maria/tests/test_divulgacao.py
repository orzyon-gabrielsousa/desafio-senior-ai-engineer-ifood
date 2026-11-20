"""Planejamento de divulgação com margem atual e volumes identificados."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sabor_da_maria import divulgacao, falas, jornada, tools, vendas
from sabor_da_maria.store import Store

from ._base import StoreTestCase

AGORA = datetime.fromisoformat("2026-12-01T12:00:00-03:00")


class PlanejamentoDivulgacaoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()
        self.store.perfil_salvar({"equipamentos": {"forno": True, "forma": True},
                                  "tecnicas": {"assar": "sim"},
                                  "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "botijão",
                                                 "energia": "estável", "espaco_geladeira": "médio"}})

    def ficha(self, nome, preco, porcoes=4):
        prato, = self.store.selecionar_pratos([{"nome": nome}])
        dados = {"ingredientes": [{"nome": "Batata", "quantidade": 230, "unidade": "g"}],
                 "tempo_preparo_min": 40, "equipamentos_necessarios": ["forno", "forma"], "tecnicas_necessarias": ["assar"]}
        receita = self.store.salvar_receita(nome, None, porcoes, dados, "conferida", prato_id=prato["id"])
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], self.store.obter_receita(receita))
        self.assertEqual(aval["veredito"], "viavel", aval)
        custo = aval["resultado"]["cmv_total"]
        self.store.salvar_prato(nome, receita, porcoes, custo, round(custo / porcoes, 2), dados, prato_id=prato["id"])
        self.store.aceitar_prato(prato["id"], preco, [], tools._reservas_de(aval["resultado"]))
        return prato["id"], custo

    def venda(self, prato, quantidade, data):
        return vendas.registrar(self.store, {"prato_id": prato, "quantidade": quantidade, "vendido_em": data,
                                             "operacao": str(uuid4())}, agora=AGORA)["venda"]

    def fotografia(self):
        with self.store.connect() as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                    for t in ("config", "ingredientes", "pratos", "reservas", "compras", "carrinho", "vendas")}

    def test_inicia_sem_historico_e_sem_perguntas_com_um_lote_hipotetico(self):
        prato, custo = self.ficha("Batata da casa", 9.50)
        antes = self.fotografia()
        plano = divulgacao.planejar(self.store, {}, agora=AGORA)
        self.assertEqual(plano["prato"]["id"], prato)
        self.assertEqual(plano["volume"], {"origem": "lote_hipotetico", "porcoes": 4, "dias": 7})
        self.assertTrue(plano["investimento_hipotetico"])
        self.assertEqual(plano["cenarios"][0]["sobra_no_periodo"], round(9.50 * 0.9 * 4 - custo, 2))
        self.assertGreater(plano["cenarios"][1]["investimento"], 0)
        self.assertIn("volume hipotético", plano["diga_a_dona_maria"])
        self.assertNotIn("?", plano["diga_a_dona_maria"])
        self.assertEqual(self.fotografia(), antes)

    def test_escolhe_volume_confirmado_por_prato_e_respeita_meia_noite_local(self):
        vendido, _ = self.ficha("Batata dourada", 8.50)
        maior_margem, _ = self.ficha("Batata especial", 20)
        self.venda(vendido, 3, "2026-11-24T03:00:00+00:00")
        self.venda(vendido, 25, "2026-11-24T02:59:59+00:00")
        self.venda(maior_margem, 30, "2026-12-01T00:00:00-03:00")
        cancelada = self.venda(maior_margem, 12, "2026-11-30T20:13:00-03:00")
        vendas.estornar(self.store, cancelada["id"], agora=AGORA)

        plano = divulgacao.planejar(self.store, {}, agora=AGORA)
        self.assertEqual(plano["prato"]["id"], vendido)
        self.assertEqual(plano["volume"]["origem"], "historico")
        self.assertEqual(plano["volume"]["porcoes"], 3)
        self.assertEqual(plano["volume"]["inicio"], "2026-11-24T00:00:00-03:00")
        self.assertEqual(plano["volume"]["fim"], "2026-12-01T00:00:00-03:00")
        focado = divulgacao.planejar(self.store, {"prato_id": maior_margem, "periodo": "dia"}, agora=AGORA)
        self.assertEqual(focado["prato"]["id"], maior_margem)
        self.assertEqual(focado["volume"], {"origem": "lote_hipotetico", "porcoes": 4, "dias": 1})

    def test_investimento_informado_rateia_o_periodo_com_cmv_exato(self):
        prato, custo = self.ficha("Batata crocante", 13.95, porcoes=3)
        antes = self.fotografia()
        plano = divulgacao.planejar(self.store, {"prato_id": prato, "periodo": "mes", "porcoes": 20,
                                               "investimento": 100}, agora=AGORA)
        self.assertEqual(plano["volume"], {"origem": "informado", "porcoes": 20, "dias": 30})
        self.assertFalse(plano["investimento_hipotetico"])
        rateado = plano["cenarios"][1]
        self.assertEqual(rateado["rateio_por_porcao"], 5)
        margem = Decimal("13.95") * Decimal("0.90") - Decimal(str(custo)) / 3
        self.assertEqual(rateado["sobra_no_periodo"], vendas._moeda(margem * 20 - 100))
        self.assertEqual(self.fotografia(), antes)

    def test_rejeita_entrada_invalida_ou_ficha_sem_viabilidade(self):
        with self.assertRaisesRegex(ValueError, "ficha viável"):
            divulgacao.planejar(self.store, {}, agora=AGORA)
        prato, _ = self.ficha("Batata assada", 10)
        for args in ({"periodo": "ano"}, {"porcoes": 0}, {"investimento": -5}, {"investimento": 1.567}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                divulgacao.planejar(self.store, args, agora=AGORA)
        self.store.perfil_salvar({"equipamentos": {"forno": False}})
        with self.assertRaisesRegex(ValueError, "ficha viável"):
            divulgacao.planejar(self.store, {"prato_id": prato}, agora=AGORA)

    def test_preferencias_aproveitam_margem_e_verba_declaradas_sem_confundir_preco(self):
        pergunta = "Qual investimento e quantas porções previstas para a divulgação?"
        preferencias = divulgacao.parametros_da_conversa([
            falas.Fala(texto="Quero 30% de margem."),
            falas.Fala(texto="Quero cobrar R$ 14 por porção.", resposta_a=pergunta),
            falas.Fala(texto="Vendi 20 porções hoje.", resposta_a=pergunta),
        ])
        self.assertEqual(preferencias, {"margem_minima": 0.30})
        preferencias = divulgacao.parametros_da_conversa([
            falas.Fala(texto="Minha margem de lucro é de 40%."),
            falas.Fala(texto="Quero margem mínima de 35%."),
            falas.Fala(texto="Quero investir R$ 25.", resposta_a=pergunta),
            falas.Fala(texto="Espero 30 porções no mês.", resposta_a=pergunta),
        ])
        self.assertEqual(preferencias, {"margem_minima": 0.35, "investimento": 25.0,
                                         "porcoes": 30, "periodo": "mes"})

    def test_plano_persistido_conclui_etapa_preserva_estoque_e_limita_verba_pela_margem(self):
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"],
                                   "estado": "SP", "cidade": "São Paulo"})
        prato, custo = self.ficha("Batata crocante", 10)
        with self.assertRaisesRegex(ValueError, "confirme o interesse"):
            divulgacao.preparar(self.store, {}, agora=AGORA)
        jornada.responder(self.store, "interesse_divulgacao")
        antes = self.fotografia()
        plano, novo = divulgacao.preparar(self.store, {"margem_minima": .60, "investimento": 1_000}, agora=AGORA)
        self.assertTrue(novo)
        self.assertEqual(plano["prato"]["id"], prato)
        self.assertEqual(plano["campanha"]["investimento"], 14.40 - custo)
        self.assertEqual(plano["campanha"]["margem_minima"], .60)
        self.assertEqual(plano["campanha"]["lucro_previsto_por_porcao"], 5.40)
        self.assertIn("Batata crocante", plano["campanha"]["legenda"])

        reaberto = Store()
        atual = jornada.resumo(reaberto)
        self.assertTrue(atual["concluidas"]["divulgacao"])
        self.assertEqual(atual["plano_divulgacao"], plano)
        recuperado, novo = divulgacao.preparar(reaberto, {}, agora=AGORA + timedelta(days=1))
        self.assertFalse(novo)
        self.assertEqual(recuperado, plano)
        depois = self.fotografia()
        for tabela in antes.keys() - {"config"}:
            self.assertEqual(antes[tabela], depois[tabela], tabela)
