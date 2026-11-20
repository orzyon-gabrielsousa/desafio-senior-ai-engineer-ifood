"""Medidas publicadas e aparelhos alternativos preservam a ficha da fonte."""

import unittest

from sabor_da_maria import fonte, tools


class MedidasDaFonteTests(unittest.TestCase):
    # Linhas da ficha lida no caso real; as especiarias e o azeite não trazem
    # quantidade publicada, e não podem receber "1 kg" ou "1 unidade" por padrão.
    FRANGO_COM_BATATA = {
        "ingredientes": ["12 coxinhas da asa de Frango", "4 batatas médias", "1/2 cebola (picada)",
                         "2 dentes de alho (triturado)", "1/2 Xícara (chá de água)",
                         "Pimenta Calabresa", "Paprica Doce", "Sal", "Salsinha", "Azeite a gosto"]
    }

    def test_titulo_isolado_ambiguo_e_massa_com_medida_continuam_ingredientes(self):
        linhas = ["Massa", "350 g de peito de frango", "Azeitona a gosto"]
        self.assertEqual(fonte.sem_titulos_de_ingredientes(linhas), linhas)
        linhas = ["Recheio:", "350 g de peito de frango", "Azeitona a gosto", "200 g de massa"]
        self.assertEqual(fonte.sem_titulos_de_ingredientes(linhas), linhas[1:])

    def test_copia_legada_perde_so_cabecalhos_confirmados(self):
        url = "https://receitas.exemplo/pizza"
        publicadas = ["Massa", "2 ovos", "Recheio", "350 g de peito de frango", "Azeitona a gosto"]
        contrato = {"url": url, "ingredientes": publicadas}
        ingredientes = fonte.ingredientes_publicados(contrato)
        ingredientes[3]["preco_compra_estimado"] = 12
        avo = {"tipo": "ingrediente_pendente", "detalhe": "Azeitona a gosto: falta medida"}
        dados = {
            "fonte_verificada": contrato,
            "ingredientes": ingredientes,
            "ficha_original_da_fonte": True,
            "fidelidade_fonte": fonte.conferir(contrato, ingredientes, 8),
            "bloqueios": [{"tipo": "ingrediente_pendente", "detalhe": "Massa: falta medida"}, avo],
        }
        corrigida = fonte.corrigir_secoes_da_ficha(dados, 8)
        self.assertEqual(corrigida["fonte_verificada"], {"url": url, "ingredientes": [publicadas[i] for i in (1, 3, 4)]})
        self.assertEqual(corrigida["ingredientes"], [ingredientes[i] for i in (1, 3, 4)])
        self.assertEqual(corrigida["bloqueios"], [avo])
        self.assertEqual(fonte.sem_medida(corrigida["fidelidade_fonte"]), ["Azeitona a gosto"])
        self.assertEqual(dados["fonte_verificada"]["ingredientes"], publicadas)
        self.assertEqual(ingredientes[0], {"nome": "Massa"})

        com_medida = {**dados, "ingredientes": [{"nome": "Massa", "quantidade": 200, "unidade": "g"}, *ingredientes[1:]]}
        self.assertIs(fonte.corrigir_secoes_da_ficha(com_medida, 8), com_medida)

    def test_frango_com_batata_preserva_contagem_xicara_e_temperos_sem_medida(self):
        self.assertEqual(fonte.ingredientes_publicados(self.FRANGO_COM_BATATA), [
            {"nome": "coxinhas da asa de frango", "quantidade": 12, "unidade": "un"},
            {"nome": "batatas medias", "quantidade": 4, "unidade": "medias"},
            {"nome": "cebola picada", "quantidade": 0.5, "unidade": "un"},
            {"nome": "alho triturado", "quantidade": 2, "unidade": "dentes"},
            {"nome": "agua", "quantidade": 0.5, "unidade": "xicara cha"},
            {"nome": "Pimenta Calabresa"}, {"nome": "Paprica Doce"}, {"nome": "Sal"},
            {"nome": "Salsinha"}, {"nome": "Azeite a gosto"},
        ])

    def test_meia_cebola_e_meia_xicara_de_agua_nao_sao_meio_quilo_e_meio_litro(self):
        contrato = {"ingredientes": ["1/2 cebola (picada)", "1/2 Xícara (chá de água)", "Azeite a gosto"]}
        ingredientes = [{"nome": "cebola picada", "quantidade": 0.5, "unidade": "kg"},
                        {"nome": "água", "quantidade": 0.5, "unidade": "L"}]
        conferencia = fonte.conferir(contrato, ingredientes, 4)
        self.assertTrue(conferencia["bloqueios"])
        self.assertEqual(conferencia["faltantes"], ["Azeite a gosto"])
        self.assertTrue(any("cebola" in d and "0.5 kg" in d for d in conferencia["diferencas"]))
        self.assertTrue(any("água" in d and "0.5 L" in d for d in conferencia["diferencas"]))

        # A conversão caseira continua explícita: meia cebola média ≈ 75 g e
        # meia xícara de chá = 120 ml, conforme a tabela de unidades existente.
        ingredientes = [{"nome": "cebola picada", "quantidade": 75, "unidade": "g"},
                        {"nome": "água", "quantidade": 120, "unidade": "ml"},
                        {"nome": "Azeite a gosto"}]
        self.assertEqual(fonte.conferir(contrato, ingredientes, 4)["bloqueios"], [])

    def test_linha_copiada_como_nome_nao_duplica_medida_nem_muda_quantidade(self):
        contrato = {"ingredientes": ["1 colher (sopa) de limão", "2 xícaras de arroz branco cozido"]}
        entrada = [{"nome": "1 colher (sopa) de limão", "quantidade": 1, "unidade": "colher de sopa"},
                   {"nome": "2 xícaras de arroz branco cozido", "quantidade": 2, "unidade": "xícara"}]
        limpa = fonte.nomes_dos_ingredientes(contrato, entrada)
        self.assertEqual(limpa[0]["nome"], "limao")
        self.assertEqual(fonte.conferir(contrato, limpa, 5)["bloqueios"], [])
        limpa[0]["quantidade"] = 2
        self.assertTrue(fonte.conferir(contrato, limpa, 5)["bloqueios"])
        self.assertEqual(entrada[0]["nome"], "1 colher (sopa) de limão")

    def test_envelope_do_transporte_preserva_titulo_da_receita_e_separa_proxima(self):
        ficha = ("# Marmita com legumes\nFonte: https://receitas.exemplo/marmita\n"
                 "Rendimento: 6 | Tempo total: 60 min\n## Ingredientes\n- 500 g de feijão\n"
                 "## Modo de preparo\nCozinhe em panela de pressão.\n")
        pagina = {"url": "https://receitas.exemplo/marmita", "content":
                  "# Receita\nFonte: https://receitas.exemplo/marmita\n\n" + ficha +
                  "# Outra receita\n## Ingredientes\n- 1 kg de carne\n## Modo de preparo\nAsse."}
        contrato = fonte.contrato_pagina(pagina)
        self.assertEqual(contrato["nome"], "Marmita com legumes")
        self.assertEqual(contrato["ingredientes"], ["500 g de feijão"])
        self.assertEqual(contrato["modo_preparo"], "Cozinhe em panela de pressão.")
        self.assertEqual(contrato["porcoes"], 6)
        self.assertEqual(contrato["tempo_preparo_min"], 60)

    def test_conversoes_equivalentes_das_medidas_publicadas(self):
        casos = (
            ("2 colheres (sopa) de óleo", "óleo", 2, "colher de sopa"),
            ("1 colher (chá) de sal", "sal", 1, "colher de chá"),
            ("1 xícara (chá) de arroz", "arroz", 1, "xícara"),
            ("1 kg de frango", "frango", 1000, "g"),
            ("1kg e 1/2 de frango", "frango", 1500, "g"),
            ("1/2 kg de frango", "frango", 500, "g"),
            ("½ kg de frango", "frango", 500, "g"),
            ("2 batatas grandes", "batata", 2, "grande"),
        )
        for linha, nome, qtd, unidade in casos:
            with self.subTest(linha=linha):
                conferencia = fonte.conferir({"ingredientes": [linha]},
                                            [{"nome": nome, "quantidade": qtd, "unidade": unidade}], 4)
                self.assertEqual(conferencia["bloqueios"], [], conferencia)

    def test_copo_americano_peito_e_medidas_publicadas_repetidas(self):
        linhas = ["2 copos americanos de arroz cozido", "1 peito de frango cozido e desfiado",
                  "2 colheres (sopa) de óleo", "2 colheres de sopa de óleo", "1 copo a leite"]
        publica = {"ingredientes": linhas}
        self.assertEqual(fonte.ingredientes_publicados(publica), [
            {"nome": "arroz cozido", "quantidade": 2, "unidade": "copos americanos"},
            {"nome": "peito de frango cozido e desfiado", "quantidade": 1, "unidade": "peito"},
            {"nome": "oleo", "quantidade": 4, "unidade": "colheres sopa"},
            {"nome": "leite", "quantidade": 1, "unidade": "copo"},
        ])
        conferida = [{"nome": "arroz cozido", "quantidade": 266, "unidade": "g"},
                     {"nome": "peito de frango cozido e desfiado", "quantidade": 1, "unidade": "peito"},
                     {"nome": "óleo", "quantidade": 60, "unidade": "ml"},
                     {"nome": "leite", "quantidade": 200, "unidade": "ml"}]
        self.assertEqual(fonte.conferir(publica, conferida, 6)["diferencas"], [])
        self.assertEqual(publica["ingredientes"], linhas)

    def test_medidas_ambiguas_ou_divisao_por_zero_nao_perdem_a_pendencia(self):
        for linha in ("1/0 kg de frango", "1kg e 1/0 de frango", "1½ kg de frango"):
            with self.subTest(linha=linha):
                conferencia = fonte.conferir({"ingredientes": [linha]},
                                            [{"nome": "frango", "quantidade": 1, "unidade": "kg"}], 4)
                self.assertTrue(conferencia["bloqueios"], conferencia)


class AdaptacaoDaFonteTests(unittest.TestCase):
    FONTE = {"ingredientes": ["400 g de frango", "1 colher (sopa) de limão", "2 xícaras de arroz cozido"]}
    PARCIAL = [{"nome": "frango", "quantidade": 400, "unidade": "g"}]

    def test_ausencia_de_adaptacao_nao_libera_ingredientes_omitidos(self):
        for resposta in (None, False, "", "não há", "não há adaptação", "nenhuma", "Sem adaptação.",
                         "não se aplica", "N/A", "sem alterações", "receita original"):
            with self.subTest(resposta=resposta):
                self.assertEqual(fonte.normalizar_adaptacao(resposta), "")
                conferencia = fonte.conferir(self.FONTE, self.PARCIAL, 4, resposta)
                self.assertIsNone(conferencia["adaptacao"])
                self.assertEqual(conferencia["faltantes"], self.FONTE["ingredientes"][1:])
                self.assertEqual(conferencia["bloqueios"][0]["tipo"], "fonte_incompleta")

    def test_negacao_de_ingrediente_em_troca_afirmativa_continua_adaptacao(self):
        for proposta in ("Não usar limão; trocar por vinagre.", "Não há limão, vou usar vinagre.",
                         "Versão sem arroz, com batatas no acompanhamento."):
            with self.subTest(proposta=proposta):
                self.assertEqual(fonte.normalizar_adaptacao(proposta), proposta)
                conferencia = fonte.conferir(self.FONTE, self.PARCIAL, 4, proposta)
                self.assertEqual(conferencia["adaptacao"], proposta)
                self.assertEqual(conferencia["faltantes"], self.FONTE["ingredientes"][1:])
                self.assertFalse(conferencia["bloqueios"])


class AparelhosDaFonteTests(unittest.TestCase):
    def requisitos(self, preparo):
        return fonte.requisitos({"modo_preparo": preparo})

    def test_assar_na_air_fryer_nao_exige_forno_convencional(self):
        for preparo in ("Asse na air fryer por 20 minutos.", "Coloque na airfryer e asse até dourar."):
            with self.subTest(preparo=preparo):
                equip, tec = self.requisitos(preparo)
                self.assertEqual(equip, ["air fryer"])
                self.assertIn("assar", tec)

    def test_refogar_na_panela_exige_fogao_mesmo_com_forno_em_outra_etapa(self):
        for preparo in ("Refogue o alho e a cebola numa panela.", "Refogue o alho na panela. Depois leve a assadeira ao forno."):
            with self.subTest(preparo=preparo):
                equip, tec = self.requisitos(preparo)
                self.assertIn("fogão", equip)
                self.assertIn("refogar", tec)

    def test_aparelho_explicito_nao_ganha_fogao_adicional(self):
        for preparo, aparelho in (("Refogue a cebola no micro-ondas.", "micro-ondas"),
                                  ("Refogue a cebola no forno.", "forno")):
            with self.subTest(preparo=preparo):
                equip, _ = self.requisitos(preparo)
                self.assertIn(aparelho, equip)
                self.assertNotIn("fogão", equip)

    def test_fonte_com_opcoes_de_aparelho_exige_apenas_um_disponivel(self):
        equip, _ = self.requisitos("Asse no forno ou na air fryer até dourar.")
        self.assertEqual(equip, ["forno ou air fryer"])
        perfil = {"equipamentos": {"forno": False, "air_fryer": True}}
        self.assertEqual(tools._checar_equipamentos(perfil, equip),
                         {"ok": ["air fryer"], "faltando": [], "nao_informado": []})
        perfil["equipamentos"]["air_fryer"] = False
        self.assertEqual(tools._checar_equipamentos(perfil, equip)["faltando"], equip)
        del perfil["equipamentos"]["air_fryer"]
        self.assertEqual(tools._checar_equipamentos(perfil, equip)["nao_informado"], equip)

    def test_comida_fria_nao_ganha_equipamento_quente(self):
        self.assertEqual(self.requisitos("Misture a salada e sirva."), ([], []))
