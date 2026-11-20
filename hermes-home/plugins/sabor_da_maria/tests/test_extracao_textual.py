"""Fonte textual direta continua verificável sem inventar JSON-LD ou medidas.

Recorte funcional do resultado público entregue pelo web_extract no caso real:
duas instruções curtas e os ingredientes, sem o restante do artigo publicado.
"""

import unittest
from unittest.mock import patch

from sabor_da_maria import extract, fonte


URL = "https://panelinha.com.br/receita/frango-com-quiabo"
TITULO = "Frango com quiabo - Panelinha"
INGREDIENTES = [
    "4 coxas de frango com pele e osso", "4 sobrecoxas de frango com pele e osso",
    "200 g de quiabo (cerca de 20 unidades)", "1 pimentão amarelo", "1 tomate italiano maduro",
    "1 cebola", "2 dentes de alho", "caldo de 2 limões", "1 colher (chá) de cominho em pó",
    "1 colher (chá) de colorau", "2½ xícaras (chá) de água", "azeite a gosto",
    "sal e pimenta-do-reino moída na hora a gosto",
]
PASSOS = ["Descasque e pique fino a cebola e o alho.", "Lave e seque bem os quiabos com um pano de prato limpo."]
TEXTO = ("#\n\nTem mais ideias aqui!\n\nPeito de frango assado para sanduíche\n\nIngredientes\n\n"
         + "\n\n".join("- " + i for i in INGREDIENTES)
         + "\n\nContinua após patrocínio\n\nModo de preparo\n\n"
         + "\n\n".join("- " + p for p in PASSOS)
         + "\n\nPode congelar\n\nInformações sobre conservação.\n\nCompre o que você precisa\n\nLoja Panelinha"
         + "\n\nTempo de preparo\n\nAté 2h\n\nServe\n\nAté 6 porções")


class ExtracaoTextualTests(unittest.TestCase):
    def pagina(self, texto=TEXTO, **extras):
        return {"url": URL, "title": TITULO, "content": texto, **extras}

    def test_recorte_real_isola_ingredientes_e_preparo_sem_trocar_cortes(self):
        ficha = fonte.contrato_pagina(self.pagina())
        self.assertEqual(ficha["ingredientes"], INGREDIENTES)
        self.assertEqual(ficha["modo_preparo"].splitlines(), PASSOS)
        self.assertEqual(ficha["nome"], TITULO)
        self.assertEqual(ficha["url"], URL)
        self.assertNotIn("Peito de frango", "\n".join(ficha["ingredientes"]))
        self.assertNotIn("Loja", ficha["modo_preparo"])
        self.assertNotIn("conservação", ficha["modo_preparo"])
        self.assertIsNone(ficha["porcoes"])
        self.assertIsNone(ficha["tempo_preparo_min"])
        self.assertEqual(ficha["rendimento_texto"], "Até 6 porções")
        self.assertEqual(ficha["tempo_preparo_texto"], "Até 2h")

    def test_coletanea_ou_multiplas_secoes_nao_viram_receita_unica(self):
        self.assertIsNone(fonte.contrato_pagina(self.pagina(title="10 receitas fáceis para o almoço")))
        for url in ("https://panelinha.com.br/receitas", "https://cozinha.exemplo/",
                    "https://cozinha.exemplo/categoria/pratos", "https://cozinha.exemplo/receitas.html"):
            with self.subTest(url=url):
                self.assertIsNone(fonte.contrato_pagina(self.pagina(url=url)))
        duplicada = TEXTO + "\nIngredientes\n- 1 batata\nModo de preparo\n- Cozinhe."
        self.assertIsNone(fonte.contrato_pagina(self.pagina(duplicada)))

    def test_aceita_pagina_textual_direta_de_outro_dominio_e_rota(self):
        for url in ("https://cozinha.exemplo/frango-com-quiabo", "https://blog.exemplo/pratos/frango-com-quiabo.html"):
            with self.subTest(url=url):
                ficha = fonte.contrato_pagina(self.pagina(url=url, title="Frango com quiabo"))
                self.assertEqual(ficha["url"], url)
                self.assertEqual(ficha["ingredientes"], INGREDIENTES)
                self.assertEqual(ficha["modo_preparo"].splitlines(), PASSOS)

    def test_fallback_incompleto_ou_continuacao_ambigua_nao_trunca_preparo(self):
        sem_preparo = TEXTO.split("Modo de preparo", 1)[0]
        self.assertIsNone(fonte.contrato_pagina(self.pagina(sem_preparo)))
        ambiguo = TEXTO.replace("\n\nPode congelar", "\n\nFinalize o cozimento antes de servir.\n\nPode congelar")
        self.assertIsNone(fonte.contrato_pagina(self.pagina(ambiguo)))

    def test_so_rotulos_exatos_fornecem_rendimento_e_tempo_numericos(self):
        ficha = fonte.contrato_pagina(self.pagina(TEXTO.replace("Até 2h", "30 minutos").replace("Até 6 porções", "6 porções")))
        self.assertEqual((ficha["porcoes"], ficha["tempo_preparo_min"]), (6, 30))

    def test_html_sem_schema_e_normalizado_sem_confundir_dados_com_jsonld(self):
        html = ("<html><head><title>" + TITULO + "</title></head><body><main>"
                "<h1><span>Frango com quiabo</span></h1><p>Tem mais ideias aqui!</p>"
                "<p>Peito de frango assado para sanduíche</p><p>Ingredientes</p><ul>"
                + "".join("<li>" + i + "</li>" for i in INGREDIENTES)
                + "</ul><p>Continua após patrocínio</p><p>Modo de preparo</p><ol>"
                + "".join("<li>" + p + "</li>" for p in PASSOS)
                + "</ol><p>Pode congelar</p><p>Conservação da receita.</p>"
                  "<p>Tempo de preparo</p><p>Até 2h</p><p>Serve</p><p>Até 6 porções</p></main></body></html>")
        with patch.object(extract, "fetch", return_value=(URL, html)):
            pagina = extract.extract_page(URL)
        self.assertFalse(pagina["metadata"]["recipe_schema"])
        self.assertTrue(pagina["metadata"]["recipe_textual"])
        ficha = fonte.contrato_pagina(pagina)
        self.assertEqual(ficha["ingredientes"], INGREDIENTES)
        self.assertEqual(ficha["modo_preparo"].splitlines(), PASSOS)
        self.assertIn("## Ingredientes", pagina["content"])
        self.assertIn("## Modo de preparo", pagina["content"])
        self.assertNotIn("Tem mais ideias", pagina["content"])
        self.assertNotIn("Peito de frango assado", pagina["content"])
