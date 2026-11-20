"""A jornada continua a escolha atual sem perder propostas nem saltar montagem."""

from sabor_da_maria import jornada, montagem, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class JornadaFocoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"],
                                  "estado": "SP", "cidade": "São Paulo"})
        self.store.perfil_salvar({"equipamentos": {"forno": True, "forma": True},
                                 "tecnicas": {"assar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "botijão",
                                                "energia": "estável", "espaco_geladeira": "médio"}})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")

    def ficha_viavel(self, nome):
        escolhido, = self.store.selecionar_pratos([{"nome": nome}])
        dados = {"ingredientes": [{"nome": "Batata", "quantidade": 200, "unidade": "g"}],
                 "tempo_preparo_min": 40, "equipamentos_necessarios": ["forno", "forma"],
                 "tecnicas_necessarias": ["assar"]}
        rid = self.store.salvar_receita(nome, None, 2, dados, "conferida", prato_id=escolhido["id"])
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], self.store.obter_receita(rid))
        self.assertEqual(aval["veredito"], "viavel", aval)
        total = aval["resultado"]["cmv_total"]
        self.store.salvar_prato(nome, rid, 2, total, total / 2, dados, prato_id=escolhido["id"])
        return escolhido["id"]

    def test_novo_prato_proprio_fica_em_foco_e_proposta_anterior_continua_salva(self):
        anterior = self.ficha_viavel("Batata assada sugerida")
        antiga = self.store.obter_prato(anterior)
        saldo = self.store.carrinho_resumo()
        jornada.responder(self.store, "prato_proprio")
        novo, = self.store.selecionar_pratos([{"nome": "Meu prato de batata"}])

        for store in (self.store, Store(self.store.db_path, self.store.xlsx_path)):
            atual = jornada.resumo(store)
            self.assertFalse(atual["controle"]["proprio_pendente"])
            self.assertEqual(atual["prato_id"], novo["id"])
            self.assertEqual(atual["pratos_ativos"], [novo["id"]])
            self.assertEqual(atual["passo"], "montagem")
            self.assertEqual(store.obter_prato(anterior), antiga)
            self.assertEqual(store.carrinho_resumo(), saldo)
            self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual({p["id"] for p in self.store.pratos_em_andamento()}, {anterior, novo["id"]})

    def test_segunda_ficha_e_montada_antes_de_precificar_a_primeira(self):
        primeira = self.ficha_viavel("Batata assada sugerida")
        self.store.salvar_analise_preco(primeira, {"cotacao": "primeira-conferida", "cenarios": []})
        segunda, = self.store.selecionar_pratos([{"nome": "Segunda batata da casa"}])
        antes = self.store.pratos()

        atual = jornada.resumo(self.store)
        self.assertEqual(atual["passo"], "montagem")
        self.assertFalse(atual["concluidas"]["montagem"])
        self.assertEqual(atual["prato_id"], segunda["id"])
        self.assertEqual(set(atual["pratos_ativos"]), {primeira, segunda["id"]})
        self.assertEqual(self.store.pratos(), antes)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertIsNone(self.store.obter_prato(primeira)["preco_venda"])

        # Só depois de as duas versões estarem aceitas para precificar, entra
        # a quinta etapa. A leitura não escolhe nem registra preço por ela.
        self.ficha_viavel(segunda["nome"])
        self.store.salvar_analise_preco(segunda["id"], {"cotacao": "segunda-conferida", "cenarios": []})
        pronto = jornada.resumo(self.store)
        self.assertTrue(pronto["concluidas"]["montagem"])
        self.assertEqual(pronto["passo"], "divulgacao")
        self.assertEqual(pronto["etapa"], "preco")
        self.assertTrue(all(p["preco_venda"] is None for p in self.store.pratos_em_andamento()))

    def test_alternativa_de_frango_preserva_fonte_e_rascunho_sem_comprometer_saldo(self):
        sessao = "adaptacao-jornada"
        original = {
            "nome": "Frango com quiabo da cidade", "url": "https://exemplo.com/frango-quiabo",
            "porcoes": 4, "tempo_preparo_min": 45,
            "ingredientes": ["480 g de peito de frango", "200 g de quiabo", "1 cebola", "10 g de alho", "4 g de sal"],
            "modo_preparo": "Doure o frango com cebola e alho. Junte o quiabo e cozinhe até ficar pronto.",
        }
        rid = self.store.salvar_receita(original["nome"], original["url"], 4,
            {"fonte_verificada": original, "sessao_da_conferencia": sessao,
             "ingredientes": [{"nome": "Peito de frango", "quantidade": 480, "unidade": "g"},
                              {"nome": "Quiabo", "quantidade": 200, "unidade": "g"}],
             "tempo_preparo_min": 45, "equipamentos_necessarios": ["fogão", "panela grande"],
             "tecnicas_necessarias": ["refogar"], "modo_preparo": original["modo_preparo"]},
            "pendente", criar_rascunho=True)
        receita_original = self.store.obter_receita(rid)
        pid = receita_original["dados"]["prato_id"]
        prato_original = self.store.obter_prato(pid)
        saldo = self.store.carrinho_resumo()
        estoque = self.store.ingredientes()
        montagem.iniciar_turno(sessao, "alternativa-sem-compras")

        proposta = montagem.propor_alternativa(self.store, original["nome"], sessao)
        self.assertIsNotNone(proposta)
        self.assertIn("Adaptação local de", proposta)
        self.assertIn(original["url"], proposta)
        self.assertIn("Diferenças da fonte", proposta)
        self.assertIn("quiabo", proposta.lower())
        self.assertIn("aceita preparar", proposta)
        novos = [p for p in self.store.pratos_em_andamento() if p["id"] != pid]
        self.assertEqual(len(novos), 1)
        nova = novos[0]
        self.assertEqual(nova["status"], "selecionado")
        self.assertIsNone(nova["preco_venda"])
        adaptada = self.store.obter_receita(nova["receita_id"])
        self.assertEqual(adaptada["dados"]["fonte_verificada"], original)
        self.assertEqual(adaptada["dados"]["proposta_anterior_id"], pid)
        self.assertTrue(adaptada["dados"]["fidelidade_fonte"]["diferencas"])
        self.assertNotEqual(adaptada["dados"]["modo_preparo"], original["modo_preparo"])
        self.assertEqual(self.store.obter_prato(pid), prato_original)
        self.assertEqual(self.store.obter_receita(rid), receita_original)
        self.assertEqual(self.store.ingredientes(), estoque)
        self.assertEqual(self.store.carrinho_resumo(), saldo)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM compras").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vendas").fetchone()[0], 0)
        for store in (self.store, Store(self.store.db_path, self.store.xlsx_path)):
            atual = jornada.resumo(store)
            self.assertEqual(atual["prato_id"], nova["id"])
            self.assertEqual(atual["pratos_ativos"], [nova["id"]])
            self.assertIn(pid, atual["controle"]["rascunhos_adiados"])
