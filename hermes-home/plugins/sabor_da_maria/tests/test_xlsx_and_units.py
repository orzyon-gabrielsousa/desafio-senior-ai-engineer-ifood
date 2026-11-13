import unittest

from sabor_da_maria.units import format_base, normalize_measure, parse_pantry_unit, to_base
from sabor_da_maria.xlsx_reader import read_table, read_workbook

from ._base import XLSX


class XlsxReaderTests(unittest.TestCase):
    def test_reads_both_sheets_with_37_ingredients(self):
        wb = read_workbook(XLSX)
        self.assertEqual(set(wb), {"Despensa", "Precos"})
        despensa = read_table(XLSX, "Despensa")
        precos = read_table(XLSX, "Precos")
        self.assertEqual(len(despensa), 37)
        self.assertEqual(len(precos), 37)
        self.assertEqual(despensa[0]["Ingrediente"], "Arroz branco tipo 1")
        self.assertEqual(despensa[0]["Quantidade em estoque"], 5)
        self.assertEqual(precos[0]["Preço total pago (R$)"], 24.9)
        self.assertAlmostEqual(sum(float(r["Preço total pago (R$)"]) for r in precos), 663.39, places=2)


class PantryUnitTests(unittest.TestCase):
    def test_simple_units(self):
        self.assertEqual(parse_pantry_unit("kg").base, "kg")
        self.assertEqual(parse_pantry_unit("L").factor, 1.0)
        self.assertEqual(parse_pantry_unit("un").base, "un")

    def test_compound_units_normalize_to_base(self):
        balde = parse_pantry_unit("balde 2kg")
        self.assertEqual((balde.base, balde.factor, balde.pack_label), ("kg", 2.0, "balde"))
        self.assertEqual(parse_pantry_unit("un 500g").factor, 0.5)
        self.assertEqual(parse_pantry_unit("un 400g").factor, 0.4)
        self.assertEqual((parse_pantry_unit("un 500ml").base, parse_pantry_unit("un 500ml").factor), ("L", 0.5))
        self.assertEqual(parse_pantry_unit("un 100ml").factor, 0.1)

    def test_unknown_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_pantry_unit("caixa misteriosa")


class RecipeConversionTests(unittest.TestCase):
    def test_measure_normalization(self):
        self.assertEqual(normalize_measure("Colheres (sopa)"), "colher de sopa")
        self.assertEqual(normalize_measure("xícaras de chá"), "xicara de cha")
        self.assertEqual(normalize_measure("dentes"), "dente")
        self.assertEqual(normalize_measure("g."), "g")

    def test_mass_and_volume(self):
        c = to_base(200, "g", "Farinha de trigo", "kg")
        self.assertAlmostEqual(c.qty_base, 0.2)
        self.assertFalse(c.estimate)
        c = to_base(2, "colheres de sopa", "Óleo de soja", "L")
        self.assertAlmostEqual(c.qty_base, 0.030)
        self.assertTrue(c.estimate)
        c = to_base(1, "xícara", "Farinha de trigo", "kg")
        self.assertAlmostEqual(c.qty_base, 0.240 * 0.55, places=4)
        self.assertIn("densidade", c.note)

    def test_count_units_use_unit_weight_table(self):
        self.assertAlmostEqual(to_base(1, "média", "Cebola", "kg").qty_base, 0.150)
        self.assertAlmostEqual(to_base(3, "dentes", "Alho", "kg").qty_base, 0.015)
        self.assertAlmostEqual(to_base(2, "fatias", "Bacon", "kg").qty_base, 0.030)
        self.assertIsNone(to_base(1, "pacote", "Sal", "kg").qty_base)

    def test_unit_counted_items(self):
        self.assertEqual(to_base(2, "un", "Ovos", "un").qty_base, 2)
        self.assertAlmostEqual(to_base(100, "g", "Ovos", "un").qty_base, 2.0)
        sem_conteudo = to_base(200, "g", "Cobertura de chocolate", "un")
        self.assertIsNone(sem_conteudo.qty_base)
        self.assertIn("despensa_atualizar", sem_conteudo.note)
        com_conteudo = to_base(200, "g", "Cobertura de chocolate", "un", conteudo_un_base=1.0)
        self.assertAlmostEqual(com_conteudo.qty_base, 0.2)

    def test_format_base(self):
        self.assertEqual(format_base(0.5, "kg"), "500 g")
        self.assertEqual(format_base(2, "kg"), "2 kg")
        self.assertEqual(format_base(0.015, "L"), "15 ml")
        self.assertEqual(format_base(3, "un"), "3 un")


if __name__ == "__main__":
    unittest.main()
