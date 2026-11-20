import unittest
from unittest.mock import patch

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
    def test_schema_com_grupos_da_pizza_mantem_so_as_linhas_de_ingredientes(self):
        html = HTML.replace(
            '"1 kg de peito de frango","2 batatas médias","3 dentes de alho","Sal a gosto"',
            '"Massa","2 xícaras de arroz","1 ovo","Recheio","350 g de peito de frango","Azeitona a gosto"',
        )
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/pizza", html)):
            pagina = extract.extract_page("https://exemplo.com/pizza")
        ficha, = pagina["receitas"]
        self.assertEqual(ficha["ingredientes"], ["2 xícaras de arroz", "1 ovo", "350 g de peito de frango", "Azeitona a gosto"])
        self.assertNotIn("- Massa\n", pagina["content"])
        self.assertNotIn("- Recheio\n", pagina["content"])
        self.assertIn("- Azeitona a gosto", pagina["content"])

    def test_schema_completo_preserva_ficha_sem_repetir_artigo(self):
        html = HTML.replace("Uma receita simples para o dia a dia.", "Detalhe repetido de apresentação. " * 180)
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/frango", html)):
            pagina = extract.extract_page("https://exemplo.com/frango")
        self.assertNotIn("Texto da página", pagina["content"])
        self.assertNotIn("Detalhe repetido", pagina["content"])
        for item in ("1 kg de peito de frango", "2 batatas médias", "3 dentes de alho", "Sal a gosto",
                     "Tempere o frango.", "Asse por 40 minutos.", "Rendimento: 4 porções", "Tempo total: 70 min"):
            self.assertIn(item, pagina["content"])
        ficha, = pagina["receitas"]
        self.assertEqual((ficha["porcoes"], ficha["tempo_preparo_min"]), (4, 70))
        self.assertEqual(len(ficha["ingredientes"]), 4)

    def test_schema_incompleto_preserva_texto_que_pode_conter_dados_faltantes(self):
        html = HTML.replace('"totalTime":"PT1H10M",', '')
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/frango", html)):
            pagina = extract.extract_page("https://exemplo.com/frango")
        self.assertIn("Texto da página", pagina["content"])
        self.assertIn("Uma receita simples", pagina["content"])

    def test_sem_schema_preserva_fallback_textual(self):
        html = "<html><title>Receita</title><article><p>Ingredientes: arroz, água e sal.</p><p>Cozinhe até ficar macio.</p></article></html>"
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/arroz", html)):
            pagina = extract.extract_page("https://exemplo.com/arroz")
        self.assertIn("Ingredientes: arroz", pagina["content"])
        self.assertIn("Cozinhe até ficar macio", pagina["content"])
        self.assertEqual(pagina["receitas"], [])

    def test_so_tempo_de_preparo_nao_descarta_artigo_com_tempo_de_forno(self):
        html = HTML.replace('"totalTime":"PT1H10M"', '"prepTime":"PT10M"')
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/frango", html)):
            pagina = extract.extract_page("https://exemplo.com/frango")
        self.assertIn("Texto da página", pagina["content"])
        self.assertIn("Uma receita simples", pagina["content"])

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

    def test_entidades_html_do_jsonld_viram_texto(self):
        # O TudoGostoso grava "&amp;oacute;leo" no preparo; sem decodificar, a ficha mostrava isso à Dona Maria.
        html = (HTML.replace('"name":"Frango ao forno fácil"', '"name":"Frango &amp; batata ao forno f&aacute;cil"')
                    .replace("Tempere o frango.", "Coloque o &amp;oacute;leo e a &amp;aacute;gua at&amp;eacute; ferver.")
                    .replace("3 dentes de alho", "3 dentes de alho &amp;amp; sal"))
        with patch.object(extract, "fetch", return_value=("https://exemplo.com/frango", html)):
            pagina = extract.extract_page("https://exemplo.com/frango")
        ficha, = pagina["receitas"]
        self.assertEqual(ficha["nome"], "Frango & batata ao forno fácil")
        self.assertIn("3 dentes de alho & sal", ficha["ingredientes"])
        self.assertTrue(ficha["modo_preparo"].startswith("Coloque o óleo e a água até ferver."))
        self.assertNotRegex(pagina["content"].split("## Texto da página")[0], r"&\w+;")

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
