from sabor_da_maria import alternativas, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class AlternativasNoEstoqueTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.perfil_salvar({
            "equipamentos": {"fogao_bocas": 4, "frigideira": True, "forno": True, "panela_pressao": True,
                             "air_fryer": False, "liquidificador": True},
            "tecnicas": {"grelhar": "sim", "refogar": "sim"},
            "restricoes": {"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 90, "gas": "botijão", "energia": "estável"},
        })

    def receita(self, nome, ingredientes, **dados):
        return self.store.salvar_receita(nome, "https://exemplo.test/receita", 2, {
            "ingredientes": ingredientes,
            "equipamentos_necessarios": ["fogão", "frigideira"],
            "tecnicas_necessarias": ["grelhar"],
            **dados,
        }, "conferida")

    def procurar(self, receita_ou_nome):
        return alternativas.alternativa_no_estoque(self.store, receita_ou_nome, tools._avaliar_prato)

    def test_verifica_alternativa_mais_proxima_sem_gastar_ou_reservar(self):
        pronta = self.receita("Frango grelhado com arroz", [
            {"nome": "Peito de frango", "quantidade": 500, "unidade": "g"},
            {"nome": "Arroz branco", "quantidade": 300, "unidade": "g"},
        ])
        original = self.receita("Frango grelhado com arroz e feijão", [
            {"nome": "Peito de frango", "quantidade": 3, "unidade": "kg"},
            {"nome": "Arroz branco", "quantidade": 300, "unidade": "g"},
            {"nome": "Feijão carioquinha", "quantidade": 200, "unidade": "g"},
        ])
        self.receita("Frango grelhado com arroz e feijao", [
            {"nome": "Peito de frango", "quantidade": 500, "unidade": "g"},
            {"nome": "Arroz branco", "quantidade": 300, "unidade": "g"},
        ])
        alternativa = self.procurar(self.store.obter_receita(original))
        self.assertEqual((alternativa["receita_id"], alternativa["compras_estimadas"], alternativa["cmv_total"]), (pronta, 0, 8.49))
        self.assertEqual(alternativa["ingredientes"], [{"nome": "Peito de frango", "quantidade": "500 g"},
                                                      {"nome": "Arroz branco tipo 1", "quantidade": "300 g"}])
        self.assertEqual(self.store.pratos_em_andamento(), [])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_recusa_outra_receita_sem_relacao_ou_com_falta_de_estoque_ou_equipamento(self):
        self.receita("Panqueca de queijo", [{"nome": "Farinha de trigo", "quantidade": 100, "unidade": "g"}])
        frango = {"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}
        self.receita("Frango grelhado com arroz", [frango], equipamentos_necessarios=["air fryer"])
        self.receita("Frango grelhado com arroz", [frango], tempo_preparo_min=120)
        self.receita("Frango grelhado com arroz", [frango, {"nome": "Brócolis", "quantidade": 100, "unidade": "g",
                                                          "preco_compra_estimado": 5, "quantidade_compra": 100, "unidade_compra": "g"}])
        self.receita("Frango grelhado com arroz", [frango, {"nome": "Arroz branco", "unidade": "g"}])
        self.assertIsNone(self.procurar("Frango grelhado com arroz e feijão"))

    def test_quantidades_repetidas_do_mesmo_item_nao_provam_disponibilidade(self):
        self.receita("Frango grelhado com arroz", [
            {"nome": "Peito de frango", "quantidade": 1.5, "unidade": "kg"},
            {"nome": "Frango", "quantidade": 1, "unidade": "kg"},
        ])
        self.assertIsNone(self.procurar("Frango grelhado com arroz e feijão"))
