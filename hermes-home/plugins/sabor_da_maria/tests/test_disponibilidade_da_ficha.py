from sabor_da_maria import jornada
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class DisponibilidadeDaFichaTests(StoreTestCase):
    def test_ficha_expoe_estoque_derivado_faltante_omitido_e_medida_desconhecida(self):
        store = Store()
        prato, = store.selecionar_pratos([{"nome": "Pizza de frango"}])
        fonte = {
            "nome": "Pizza de frango",
            "ingredientes": ["300 g de peito de frango", "150 g de molho de tomate", "5 g de fermento biológico", "alho a gosto"],
        }
        store.salvar_receita(prato["nome"], "https://exemplo.test/pizza", 4, {
            "fonte_verificada": fonte,
            "ingredientes": [
                {"nome": "peito de frango", "quantidade": 300, "unidade": "g"},
                {"nome": "molho de tomate quanto baste", "quantidade": 150, "unidade": "g"},
                {"nome": "alho a gosto"},
            ],
            "adaptacao": "Massa fina sem fermento.",
            "ingredientes_omitidos": ["1 un de 1/2 colher de fermento biológico"],
        }, "conferida", prato_id=prato["id"])

        ficha, = jornada.pratos_atuais(store)
        ingredientes = {linha["ingrediente"]: linha for linha in ficha["ingredientes_da_ficha"]}
        self.assertEqual(ingredientes["peito de frango"]["estado"], "disponivel")
        molho = ingredientes["molho de tomate"]
        self.assertEqual((molho["estado"], molho["item_despensa"], molho["derivado"]), ("disponivel", "Tomate", True))
        self.assertIsNotNone(molho["uso_despensa"])
        fermento = ingredientes["fermento biológico"]
        self.assertEqual((fermento["estado"], fermento["omitido"], fermento["quantidade"]), ("faltando", True, "5 g"))
        self.assertEqual(ingredientes["alho"]["estado"], "a_conferir")
        self.assertEqual(store.reservas_ativas(), [])
