"""Estimativas da ficha usam o lote atual e mantêm a fonte publicada."""

from copy import deepcopy
from unittest import TestCase

from sabor_da_maria.montagem import estimar_medidas_do_lote


class EstimativasDoLoteTests(TestCase):
    def montar_ficha(self):
        return {"nome": "Pizza de frango", "dados": {
            "adaptacao": "Massa fina sem fermentação.",
            "fonte_verificada": {"nome": "Pizza de frango", "ingredientes": [
                "1 kg de farinha de trigo", "100 g de cebola", "sal a gosto", "fatias de queijo a gosto",
                "molho de tomate quanto baste", "orégano a gosto"]},
            "ingredientes": [
                {"nome": "farinha de trigo", "quantidade": 0.5, "unidade": "kg"},
                {"nome": "cebola", "quantidade": 50, "unidade": "g"},
                {"nome": "sal a gosto", "quantidade": 4, "unidade": "g"},
                {"nome": "quantidade não informada de fatias de queijo a gosto", "quantidade": 0, "item_despensa": "Queijo mussarela"},
                {"nome": "molho de tomate quanto baste"},
            ],
            "medidas_de_uso_confirmadas": {"sal a gosto": {"quantidade": 4, "unidade": "g"}},
        }}

    def test_estima_as_medidas_juntas_com_base_na_farinha_do_lote_adaptado(self):
        receita = self.montar_ficha()
        anterior = deepcopy(receita)
        lote, estimativas = estimar_medidas_do_lote(receita)
        queijo = "quantidade não informada de fatias de queijo a gosto"
        self.assertEqual(receita, anterior)
        self.assertEqual(lote[:3], anterior["dados"]["ingredientes"][:3])
        self.assertEqual(lote[3], {"nome": queijo, "quantidade": 150, "unidade": "g", "item_despensa": "Queijo mussarela"})
        self.assertEqual(lote[4], {"nome": "molho de tomate quanto baste", "quantidade": 150, "unidade": "g"})
        self.assertEqual(set(estimativas), {queijo, "molho de tomate quanto baste"})
        self.assertIn("30% dos 500 g", estimativas[queijo]["base"])

    def test_respeita_medida_publicada_e_nao_estima_farinha_sem_peso_confirmado(self):
        receita = self.montar_ficha()
        dados = receita["dados"]
        dados["fonte_verificada"]["ingredientes"][3] = "120 g de fatias de queijo"
        _, estimativas = estimar_medidas_do_lote(receita)
        self.assertEqual(set(estimativas), {"molho de tomate quanto baste"})
        dados["ingredientes"][0].update(quantidade=3, unidade="xícara")
        lote, estimativas = estimar_medidas_do_lote(receita)
        self.assertIs(lote, dados["ingredientes"])
        self.assertEqual(estimativas, {})
