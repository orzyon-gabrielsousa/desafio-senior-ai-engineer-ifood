import unittest

from sabor_da_maria import extract

HTML = """
<html><head><title>Frango ao forno fácil - Receitas da Vovó</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"x"},
 {"@type":["Recipe"],"name":"Frango ao forno fácil","recipeYield":"4 porções","totalTime":"PT1H10M",
  "recipeIngredient":["1 kg de peito de frango","2 batatas médias","3 dentes de alho","Sal a gosto"],
  "recipeInstructions":[{"@type":"HowToStep","text":"Tempere o frango."},{"@type":"HowToSection","name":"Forno","itemListElement":[{"@type":"HowToStep","text":"Asse por 40 minutos."}]}],
  "aggregateRating":{"ratingValue":"4.7","ratingCount":"120"}}]}
</script>
<script>alert('nao deve aparecer')</script><style>.x{}</style></head>
<body><nav>Menu Home Receitas</nav>
<article><h1>Frango ao forno fácil</h1><p>Uma receita simples para o dia a dia.</p>
<ul><li>1 kg de peito de frango</li><li>2 batatas médias</li></ul>
<p>Modo de preparo: tempere e asse.</p></article>
<footer>Rodapé com links</footer></body></html>
"""


class ExtractTests(unittest.TestCase):
    def test_jsonld_recipe_is_parsed(self):
        recipes = extract.jsonld_recipes(HTML)
        self.assertEqual(len(recipes), 1)
        md = extract.recipe_markdown(recipes[0], "https://exemplo.com/frango")
        self.assertIn("# Frango ao forno fácil", md)
        self.assertIn("Rendimento: 4 porções", md)
        self.assertIn("Tempo total: 70 min", md)
        self.assertIn("- 1 kg de peito de frango", md)
        self.assertIn("1. Tempere o frango.", md)
        self.assertIn("[Forno]", md)
        self.assertIn("Avaliação: 4.7 (120 votos)", md)

    def test_html_to_text_strips_noise(self):
        title, text = extract.html_to_text(HTML)
        self.assertEqual(title, "Frango ao forno fácil - Receitas da Vovó")
        self.assertNotIn("alert", text)
        self.assertNotIn("Rodapé", text)
        self.assertIn("1 kg de peito de frango", text)

    def test_iso_duration(self):
        self.assertEqual(extract.iso_minutes("PT30M"), 30)
        self.assertEqual(extract.iso_minutes("PT1H"), 60)
        self.assertEqual(extract.iso_minutes("P1DT2H"), 1560)
        self.assertIsNone(extract.iso_minutes("abc"))

    def test_private_hosts_are_refused(self):
        self.assertTrue(extract.host_is_private("localhost"))
        self.assertTrue(extract.host_is_private("127.0.0.1"))
        self.assertTrue(extract.host_is_private("10.0.0.5"))
        with self.assertRaises(ValueError):
            extract._check_url("http://127.0.0.1:8080/x")
        with self.assertRaises(ValueError):
            extract._check_url("ftp://exemplo.com/x")
        page = extract.extract_page("http://localhost:1/x")
        self.assertIn("error", page)


if __name__ == "__main__":
    unittest.main()
