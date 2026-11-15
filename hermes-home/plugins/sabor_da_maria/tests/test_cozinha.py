"""Minha cozinha: o que ela tem e sabe fazer (tela e conversa no mesmo registro), as fichas de /prompts e os avisos de compatibilidade."""

import json
import logging
import math
import tempfile
import unittest
from pathlib import Path

from sabor_da_maria import analytics, api, costing, cozinha, falas, painel, tools
from sabor_da_maria.store import Store

from ._base import REPO, SESSAO, StoreTestCase, diz

BASE = REPO / "prompts" / "base-de-conhecimento" / "cozinha-e-equipamentos"

# A cozinha do roteiro, marcada na tela: fecha o checklist da consultora (equipamentos, rotina e ao menos uma técnica).
COZINHA_COMPLETA = {
    "equipamentos": {"fogao": 4, "forno": True, "panela_pressao": True, "air_fryer": False, "liquidificador": True},
    "habilidades": {"refogar": "sim"},
    "rotina": {"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"},
}


def call(name, **args):
    return json.loads(tools.HANDLERS[name](args, session_id=SESSAO))


def marcar(**partes):
    return Store().cozinha_salvar(partes)


class CatalogoTests(unittest.TestCase):
    def test_ids_unicos_e_grupos_conhecidos(self):
        ids = [i.id for i in cozinha.CATALOGO]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({i.grupo for i in cozinha.CATALOGO}, {cozinha.EQUIPAMENTO, cozinha.UTENSILIO, cozinha.HABILIDADE})
        for i in cozinha.itens(cozinha.HABILIDADE):
            self.assertTrue(i.frase, i.id)  # as frases dos avisos usam o verbo no infinitivo

    def test_opcoes_para_a_tela(self):
        o = cozinha.opcoes()
        self.assertEqual({i["id"] for i in o["equipamentos"]} >= {"fogao", "forno", "micro_ondas", "air_fryer"}, True)
        self.assertIn("panela_pressao", {i["id"] for i in o["utensilios"]})
        self.assertEqual([n["id"] for n in o["niveis"]], ["sim", "aprendendo", "nao"])
        self.assertEqual({r["id"] for r in o["rotina"]}, {"espaco_geladeira", "tempo_por_cozinhada_min", "gas", "energia"})

    def test_o_que_ela_diz_vira_o_item_do_catalogo(self):
        casos = {"fogão de 4 bocas": "fogao", "Panela de pressão": "panela_pressao", "micro-ondas": "micro_ondas", "airfryer": "air_fryer",
                 "assadeira": "forma", "frigideira funda": "panela_funda", "facas": "faca", "balança": "balanca", "refrigerador": "geladeira"}
        for texto, esperado in casos.items():
            self.assertEqual(cozinha.id_por_termos(texto), esperado, texto)
        self.assertEqual(cozinha.habilidade_id("ponto da carne"), "grelhar")
        self.assertEqual(cozinha.habilidade_id("molho branco"), "molhos")
        self.assertEqual(cozinha.habilidade_id("assados"), "assar")

    def test_palavra_inteira_nao_pedaco_de_palavra(self):
        self.assertIsNone(cozinha.id_por_termos("informação"))  # tem "forma" dentro, não é uma forma
        self.assertIsNone(cozinha.id_por_termos("massa fresca"))
        self.assertIsNone(cozinha.habilidade_id("forno"))  # equipamento não é habilidade

    def test_nivel_das_respostas_livres(self):
        self.assertEqual([cozinha.nivel(v) for v in (True, False, 4, 0, "sim", "não", "estou aprendendo", "quebrado", None, "")],
                         ["sim", "nao", "sim", "nao", "sim", "nao", "parcial", "nao", None, None])


class TelaGravaNoBancoTests(StoreTestCase):
    def test_o_que_ela_marca_vai_para_o_mesmo_perfil_da_consultora(self):
        marcar(equipamentos={"fogao": 4, "forno": False, "micro_ondas": True}, habilidades={"assar": "aprendendo", "grelhar": "sim"},
               rotina={"gas": "botijão", "tempo_por_cozinhada_min": 120})
        perfil = Store().perfil()  # o que a consultora lê
        self.assertEqual(perfil["equipamentos"], {"fogao": 4, "forno": False, "micro_ondas": True})
        self.assertEqual(perfil["tecnicas"], {"assar": "aprendendo", "grelhar": "sim"})
        self.assertEqual(perfil["restricoes"]["tempo_por_cozinhada_min"], 120)
        # e sobrevive a uma conexão nova (está no SQLite, não na memória)
        self.assertEqual(Store().cozinha()["equipamentos"]["forno"], False)

    def test_o_que_a_conversa_gravou_aparece_na_tela(self):
        diz("Tenho fogão de 4 bocas, forno e air fryer. Sei refogar e fazer molho branco. Nunca fiz massa fresca. O gás é de botijão.")
        out = call("perfil_cozinha_salvar", equipamentos={"fogão": 4, "forno": True, "air fryer": True},
                   tecnicas={"refogado": "sim", "molho branco": "sim", "massa fresca": "nunca fiz"}, restricoes={"gas": "botijão"})
        self.assertTrue(out["success"], out)
        tela = Store().cozinha()
        self.assertEqual(tela["equipamentos"], {"fogao": 4, "forno": True, "air_fryer": True})
        self.assertEqual(tela["habilidades"], {"refogar": "sim", "molhos": "sim"})  # as palavras dela viram o item do catálogo
        self.assertEqual(tela["rotina"], {"gas": "botijão"})
        # o que não é do catálogo não some: aparece em "outros"
        self.assertEqual([(o["grupo"], o["nome"], o["texto"]) for o in tela["outros"]], [("tecnicas", "Massa fresca", "não sei")])
        self.assertEqual(tela["resumo"]["respondidos"], 5)

    def test_a_tela_e_a_conversa_se_corrigem_uma_a_outra(self):
        diz("Tenho forno")
        call("perfil_cozinha_salvar", equipamentos={"forno": True})
        marcar(equipamentos={"forno": False})  # ela mudou de ideia na tela
        self.assertIs(Store().cozinha()["equipamentos"]["forno"], False)
        diz("Comprei um forno novo", sessao=SESSAO)
        call("perfil_cozinha_salvar", equipamentos={"forno": True})
        self.assertIs(Store().cozinha()["equipamentos"]["forno"], True)

    def test_variante_antiga_nao_deixa_duas_respostas(self):
        Store().perfil_salvar({"equipamentos": {"forno_eletrico": True, "airfryer": False}, "tecnicas": {"molho_branco": "sim"}})
        marcar(equipamentos={"forno": False}, habilidades={"molhos": "nao"})
        perfil = Store().perfil()
        self.assertEqual(perfil["equipamentos"], {"airfryer": False, "forno": False})
        self.assertEqual(perfil["tecnicas"], {"molhos": "nao"})

    def test_voltar_a_nao_respondi_e_remover_o_que_esta_errado(self):
        Store().perfil_salvar({"tecnicas": {"refogado": "sim", "massa_fresca": "nao"}, "restricoes": {"regiao": "sao paulo zona leste", "gas": "botijão"},
                               "observacoes": ["usa panela de pressão"]})
        marcar(habilidades={"refogar": None}, rotina={"gas": None},
               remover=[{"grupo": "restricoes", "chave": "regiao"}, {"grupo": "tecnicas", "chave": "massa_fresca"},
                        {"grupo": "observacoes", "chave": "usa panela de pressão"}])
        perfil = Store().perfil()
        self.assertEqual((perfil["tecnicas"], perfil["restricoes"], perfil["observacoes"]), ({}, {}, []))

    def test_so_muda_o_que_vier(self):
        marcar(equipamentos={"forno": True, "fogao": 2})
        marcar(equipamentos={"fogao": 4})
        self.assertEqual(Store().cozinha()["equipamentos"], {"forno": True, "fogao": 4})

    def test_so_entra_o_que_esta_no_catalogo(self):
        ruins = [
            {"equipamentos": {"geladeirinha": True}},
            {"equipamentos": {"assar": True}},  # habilidade não é equipamento
            {"equipamentos": {"forno": "talvez"}},
            {"equipamentos": {"forno": 3}},  # número de bocas só vale para o fogão
            {"equipamentos": {"fogao": 9}},
            {"equipamentos": [True]},
            {"habilidades": {"assar": "mais ou menos"}},
            {"habilidades": {"forno": "sim"}},
            {"rotina": {"gas": "nuclear"}},
            {"rotina": {"tempo_por_cozinhada_min": 75}},
            {"rotina": {"cor_do_fogao": "azul"}},
            {"remover": [{"grupo": "despensa", "chave": "arroz"}]},
        ]
        for dados in ruins:
            with self.assertRaises(ValueError, msg=str(dados)):
                marcar(**dados)
        with self.assertRaises(ValueError):
            Store().cozinha_salvar(["forno"])
        self.assertEqual(Store().perfil()["equipamentos"], {})  # nada entrou pela metade

    def test_cozinha_completa_na_tela_fecha_o_checklist_da_consultora(self):
        self.assertEqual(len(call("perfil_cozinha_consultar")["pendencias"]), 10)
        marcar(**COZINHA_COMPLETA)
        out = call("perfil_cozinha_consultar")
        self.assertTrue(out["completo"], out["pendencias"])
        self.assertEqual(out["perguntas_prontas"], "")
        self.assertIn("TEM: fogão (4 bocas), forno, liquidificador, panela de pressão", out["minha_cozinha"])
        self.assertIn("NÃO TEM: air fryer", out["minha_cozinha"])
        self.assertIn("não pergunte de novo", out["orientacao"])

    def test_o_que_ela_marcou_vale_para_as_travas_do_cardapio(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"forno": False})
        receita = call("receita_conferir_ingredientes", nome="Carne moída gratinada", porcoes=4,
                       ingredientes=[{"nome": "carne moída", "quantidade": 500, "unidade": "g"}], equipamentos_necessarios=["forno"], tecnicas_necessarias=["refogado"])
        self.assertEqual(receita["veredito"], "inviavel_por_enquanto")
        self.assertEqual(receita["equipamentos"]["faltando"], ["forno"])
        self.assertEqual(receita["tecnicas"]["ok"], ["refogado"])  # a habilidade marcada na tela atende o nome que a receita usa
        marcar(equipamentos={"forno": True})
        receita = call("receita_conferir_ingredientes", nome="Carne moída gratinada", porcoes=4,
                       ingredientes=[{"nome": "carne moída", "quantidade": 500, "unidade": "g"}], equipamentos_necessarios=["forno"], tecnicas_necessarias=["refogado"])
        self.assertEqual(receita["veredito"], "viavel", receita["bloqueios"])

    def test_habilidade_da_tela_atende_o_nome_que_a_receita_usa(self):
        marcar(habilidades={"molhos": "sim", "assar": "aprendendo", "empanar": "nao"})
        perfil = Store().perfil()
        r = tools._checar_tecnicas(perfil, ["molho branco", "assados", "empanado", "ponto da carne"])
        self.assertEqual((r["ok"], r["parcial"], r["faltando"], r["nao_informado"]), (["molho branco"], ["assados"], ["empanado"], ["ponto da carne"]))


class FichasDaBaseTests(unittest.TestCase):
    """As fichas do repositório sempre no formato: é este teste que acusa arquivo e linha de qualquer defeito ao editar a base."""

    def setUp(self):
        self.pratos = cozinha.analisar_pratos((BASE / "pratos.md").read_text(encoding="utf-8"), "pratos.md")
        self.regras = cozinha.analisar_ingredientes((BASE / "ingredientes.md").read_text(encoding="utf-8"), "ingredientes.md")

    def test_todo_prato_do_analytics_tem_ficha_e_vice_versa(self):
        do_analytics = {p.nome for s in analytics.carregar() for p in s.pratos}
        self.assertEqual({f.nome for f in self.pratos}, do_analytics)
        self.assertEqual(len(self.pratos), len({f.nome for f in self.pratos}))

    def test_fichas_completas(self):
        for f in self.pratos:
            self.assertGreater(f.custo_tipico, 0, f.nome)
            self.assertTrue(f.categoria, f.nome)
            self.assertTrue(f.habilidades, f"{f.nome}: toda ficha pede ao menos uma habilidade")
        self.assertGreaterEqual(len(self.regras), 10)
        for r in self.regras:
            self.assertTrue(r.para and r.aviso, r.nome)

    def test_a_base_do_repositorio_e_encontrada(self):
        self.assertIsNotNone(cozinha.raiz())
        self.assertEqual(len(cozinha.carregar().pratos), len(self.pratos))

    def test_readme_lista_todos_os_itens_do_catalogo(self):
        readme = (BASE / "README.md").read_text(encoding="utf-8")
        for item in cozinha.CATALOGO:
            self.assertIn(item.nome, readme, f"o README da pasta não lista {item.nome!r}")

    def test_nome_fora_do_catalogo_diz_arquivo_e_linha(self):
        ruins = {
            "equipamento": ("| Prato | Precisa de | Habilidades | Custo típico (R$) | Observação |\n|---|---|---|---|---|\n| Pizza | forno, tijolo | assar | 9,00 | |\n", "linha 3"),
            "habilidade": ("| Pizza | forno | voar | 9,00 | |\n", "'voar' não é uma habilidade"),
            "colunas": ("| Pizza | forno | assar |\n", "5 colunas"),
            "custo": ("| Pizza | forno | assar | grátis | |\n", "custo típico"),
        }
        for nome, (texto, trecho) in ruins.items():
            with self.assertRaises(ValueError, msg=nome) as ctx:
                cozinha.analisar_pratos(texto, "ruim.md")
            self.assertIn("ruim.md, linha", str(ctx.exception))
            self.assertIn(trecho, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            cozinha.analisar_ingredientes("| farinha | tijolo | pão | x |\n", "ing.md")
        self.assertIn("ing.md, linha 1", str(ctx.exception))

    def test_arquivo_com_defeito_nao_derruba_o_outro(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "pratos.md").write_text("| Pizza | tijolo | assar | 9,00 | |\n", encoding="utf-8")
            (pasta / "ingredientes.md").write_text((BASE / "ingredientes.md").read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertLogs("sabor_da_maria.cozinha", level="WARNING"):
                base = cozinha.carregar(pasta)
        self.assertEqual((len(base.pratos), len(base.regras) > 5), (0, True))

    def test_edicao_da_base_vale_sem_reiniciar(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "pratos.md").write_text("| Pizza de teste | forno | assar | 9,00 | |\n", encoding="utf-8")
            self.assertEqual(cozinha.carregar(pasta).pratos["pizza de teste"].custo_tipico, 9.0)
            (pasta / "pratos.md").write_text("| Pizza de teste | forno | assar | 11,50 | |\n", encoding="utf-8")
            self.assertEqual(cozinha.carregar(pasta).pratos["pizza de teste"].custo_tipico, 11.5)

    def test_achar_o_prato_pelo_nome(self):
        self.assertEqual(cozinha.ficha_do_prato("Pizza de calabresa").nome, "Pizza de calabresa")
        self.assertEqual(cozinha.ficha_do_prato("coxinha de frango").nome, "Coxinha de frango (porção com 6)")  # sem o parêntese
        self.assertEqual(cozinha.ficha_do_prato("PRATO FEITO (PF) COM BIFE").nome, "Prato feito (PF) com bife")
        self.assertEqual(cozinha.ficha_do_prato("Pastel").nome, "Pastel (porção com 4)")  # único com essas palavras
        self.assertIsNone(cozinha.ficha_do_prato("pizza"))  # ambíguo: duas pizzas
        self.assertIsNone(cozinha.ficha_do_prato("Coxinha de jaca"))
        self.assertIsNone(cozinha.ficha_do_prato(""))
        self.assertIsNone(cozinha.ficha_do_prato(None))

    def test_achar_a_regra_do_ingrediente(self):
        self.assertEqual(cozinha.regra_do_ingrediente("Massa de pizza pré-assada (2 un)").nome, "massa de pizza")
        self.assertEqual(cozinha.regra_do_ingrediente("feijão fradinho").nome, "feijão fradinho")  # o mais específico ganha
        self.assertEqual(cozinha.regra_do_ingrediente("feijão carioquinha").nome, "feijão")
        self.assertEqual(cozinha.regra_do_ingrediente("Filé de salmão").nome, "salmão")
        for livre in ("arroz", "sal", "óleo de soja", "queijo mussarela"):
            self.assertIsNone(cozinha.regra_do_ingrediente(livre), livre)


class ConferenciaTests(unittest.TestCase):
    def conferir(self, prato, perfil, ingredientes=()):
        return cozinha.conferir(prato, ingredientes, perfil)

    def test_nao_da_quando_ela_disse_que_nao_tem(self):
        c = self.conferir("Pizza de calabresa", {"equipamentos": {"forno": False, "forma": True}, "tecnicas": {"massa_fermentada": "sim", "assar": "sim"}})
        self.assertEqual(c["status"], "nao_da")
        self.assertEqual([f["precisa"] for f in c["falta"]], ["forno"])
        self.assertIn("Com a sua cozinha não dá para fazer Pizza de calabresa: ele precisa de forno, e você marcou que não tem.", c["mensagens"][0])
        self.assertIn("Os ingredientes não viram esse prato sem isso", c["mensagens"][0])

    def test_ok_quando_tem_e_sabe_tudo(self):
        c = self.conferir("Pizza de calabresa", {"equipamentos": {"forno": True, "forma": 1}, "tecnicas": {"massa_fermentada": "sim", "assar": "sim"}})
        self.assertEqual((c["status"], c["mensagens"]), ("ok", []))

    def test_atencao_quando_ela_esta_aprendendo_ou_nao_sabe(self):
        equip = {"forno": True, "forma": True}
        aprendendo = self.conferir("Pizza de calabresa", {"equipamentos": equip, "tecnicas": {"massa_fermentada": "aprendendo", "assar": "sim"}})
        self.assertEqual(aprendendo["status"], "atencao")
        self.assertIn("está aprendendo a fazer massa com fermento (pizza e pão)", aprendendo["mensagens"][0])
        nao_sabe = self.conferir("Pizza de calabresa", {"equipamentos": equip, "tecnicas": {"massa_fermentada": "nao", "assar": "sim"}})
        self.assertEqual(nao_sabe["status"], "atencao")
        self.assertIn("não sabe fazer massa com fermento", nao_sabe["mensagens"][0])

    def test_confirmar_quando_ela_ainda_nao_respondeu(self):
        c = self.conferir("Pizza de calabresa", {})
        self.assertEqual(c["status"], "confirmar")
        self.assertIn("Falta você dizer se tem forno e forma e se sabe fazer massa com fermento", c["mensagens"][0])
        self.assertEqual(c["falta"], [])

    def test_equipamento_que_nao_tem_pesa_mais_que_habilidade_e_confirmacao(self):
        c = self.conferir("Pizza de calabresa", {"equipamentos": {"forno": False}, "tecnicas": {"assar": "nao"}})
        self.assertEqual(c["status"], "nao_da")
        self.assertEqual(len(c["mensagens"]), 1)  # só o que impede: o resto fica para depois

    def test_alternativas_basta_uma(self):
        perfil = {"equipamentos": {"fogao": 4, "panela_pressao": False, "panela_grande": True, "frigideira": True}, "tecnicas": {}}
        self.assertNotIn("panela de pressão ou panela grande", [f["precisa"] for f in self.conferir("Feijoada completa", perfil)["falta"]])
        nenhuma = {"equipamentos": {"fogao": 4, "panela_pressao": False, "panela_grande": False}}
        c = self.conferir("Feijoada completa", nenhuma)
        self.assertEqual(c["status"], "nao_da")
        self.assertEqual(c["falta"][0]["precisa"], "panela de pressão ou panela grande")
        self.assertIn("ele precisa de panela de pressão ou panela grande", c["mensagens"][0])
        uma_nao_outra_nao_sei = {"equipamentos": {"fogao": 4, "panela_pressao": False}}
        self.assertEqual([f["precisa"] for f in self.conferir("Feijoada completa", uma_nao_outra_nao_sei)["confirmar"]], ["panela de pressão ou panela grande"])

    def test_funciona_mais_ou_menos_conta_como_ter(self):
        c = self.conferir("Pizza de calabresa", {"equipamentos": {"forno": "às vezes funciona", "forma": True}, "tecnicas": {"massa_fermentada": "sim", "assar": "sim"}})
        self.assertEqual(c["status"], "ok")

    def test_ingrediente_que_nao_tem_como_virar_comida(self):
        perfil = {"equipamentos": {"forno": False}}
        c = self.conferir(None, perfil, ["Massa de pizza pré-assada", "mussarela"])
        self.assertEqual(c["status"], "nao_da")
        self.assertEqual(c["falta"][0]["origem"], "ingrediente")
        self.assertEqual(
            c["mensagens"][0],
            "Massa de pizza pré-assada precisa de forno para virar pizza, e você marcou que não tem: com a sua cozinha não tem como fazer pizza "
            "com esse ingrediente. Se você tem, marque em Minha cozinha.")
        açaí = self.conferir(None, {"equipamentos": {"freezer": False, "liquidificador": True}}, ["polpa de açaí"])
        self.assertIn("precisa de freezer para virar açaí na tigela", açaí["mensagens"][0])
        self.assertEqual(self.conferir(None, {"equipamentos": {"forno": True}}, ["massa de pizza"])["status"], "ok")

    def test_sem_ficha_nada_a_conferir(self):
        c = self.conferir("Bolo de cenoura da vizinha", {}, ["cenoura", "açúcar"])
        self.assertEqual((c["status"], c["ficha"], c["mensagens"]), ("sem_ficha", False, []))

    def test_prato_sem_equipamento_exigido(self):
        c = self.conferir("Sanduíche de mortadela", {"tecnicas": {"montagem": "sim"}})
        self.assertEqual((c["status"], c["falta"]), ("ok", []))

    def test_analytics_anotado_com_a_cozinha_e_o_quanto_cobrar(self):
        ranking = [{"prato": "Pizza de calabresa", "preco_medio": 53.3}, {"prato": "Bife do dia", "preco_medio": 30.0}]
        cozinha.anotar_ranking(ranking, {"equipamentos": {"forno": False}})
        pizza, outro = ranking
        self.assertEqual(pizza["cozinha"]["status"], "nao_da")
        p = pizza["preco"]
        self.assertEqual((p["cmv"], p["custo_exemplo"]), (16.0, True))
        self.assertEqual(p["preco_minimo"], math.ceil(16.0 / 0.9 * 100) / 100)
        self.assertEqual([c["nome"] for c in p["cenarios"]], ["Conservador", "Equilibrado", "Confortável"])
        self.assertAlmostEqual(p["mercado"]["recebe_liquido"], 53.3 * 0.9, places=2)
        self.assertEqual(p["mercado"]["lucro"], round(53.3 * 0.9 - 16.0, 2))
        self.assertIn("exemplo", p["aviso"])
        self.assertEqual((outro["cozinha"]["status"], outro["preco"]), ("sem_ficha", None))  # sem ficha não há custo para inventar

    def test_texto_das_perguntas_so_tem_o_que_falta(self):
        pend = tools._pendencias_perfil({})
        todas = cozinha.perguntas_da_cozinha(pend)
        for trecho in ("1. **Equipamentos e utensílios:**", "quantas bocas", "2. **Técnicas que você domina:**", "molho béchamel", "3. **Rotina da cozinha:**",
                       "espaço sobra na geladeira", "dedicar a cada cozinhada", "gás", "energia", "Minha cozinha"):
            self.assertIn(trecho, todas)
        so_rotina = cozinha.perguntas_da_cozinha([p for p in pend if p["grupo"] == "restricoes"])
        self.assertTrue(so_rotina.splitlines()[1].startswith("1. **Rotina da cozinha:**"))
        self.assertNotIn("bocas", so_rotina)
        self.assertEqual(cozinha.perguntas_da_cozinha([]), "")


class QuantoCobrarTests(unittest.TestCase):
    def test_preco_minimo_nunca_deixa_prejuizo(self):
        for cmv in (0.01, 2.6, 9.5, 16.0, 33.33, 123.45):
            q = costing.quanto_cobrar(cmv)
            self.assertTrue(costing.avaliar_preco(q["preco_minimo"], cmv)["viavel"], cmv)  # lucro >= 0 no preço mostrado
            self.assertLessEqual(q["preco_minimo"] - cmv / 0.9, 0.0100001)

    def test_cenarios_batem_com_a_consultora(self):
        q = costing.quanto_cobrar(9.5, 29.4)
        c = costing.cenarios_preco(9.5)
        self.assertEqual([x["preco"] for x in q["cenarios"]], [x["preco_sugerido"] for x in c["cenarios"]])
        self.assertEqual([x["lucro"] for x in q["cenarios"]], [x["com_preco_sugerido"]["lucro"] for x in c["cenarios"]])
        self.assertEqual(q["mercado"]["taxa_plataforma"], 2.94)
        self.assertEqual(q["mercado"]["recebe_liquido"], 26.46)

    def test_prejuizo_no_preco_do_mercado_aparece(self):
        self.assertFalse(costing.quanto_cobrar(30.0, 25.0)["mercado"]["viavel"])
        self.assertIsNone(costing.quanto_cobrar(9.5)["mercado"])

    def test_custo_invalido(self):
        for ruim in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                costing.quanto_cobrar(ruim)


class AvisosNasToolsTests(StoreTestCase):
    PIZZA = [{"nome": "massa de pizza", "quantidade": 1, "unidade": "un", "preco_total": 8},
             {"nome": "mussarela", "quantidade": 300, "unidade": "g", "preco_total": 12}]

    def test_prato_sem_forno_avisa_mas_a_decisao_e_dela(self):
        marcar(equipamentos={"forno": False})
        out = call("carrinho_adicionar_prato", prato="Pizza de calabresa", ingredientes=self.PIZZA)
        self.assertTrue(out["success"], out)  # rascunho de compra: entra no carrinho
        self.assertEqual(out["alerta_da_cozinha"]["status"], "nao_da")
        self.assertTrue(out["diga_a_dona_maria"].startswith("Atenção: Com a sua cozinha não dá para fazer Pizza de calabresa"))
        self.assertIn("Massa de pizza precisa de forno para virar pizza", out["diga_a_dona_maria"])
        self.assertIn("saldo R$ 60,00", out["diga_a_dona_maria"])  # o aviso vem antes do prato e do saldo, que continuam lá
        self.assertEqual(len(Store().carrinho_itens()), 2)

    def test_aviso_aparece_no_estado_da_tela_enquanto_a_cozinha_nao_faz(self):
        marcar(equipamentos={"forno": False})
        Store().carrinho_adicionar_prato("Pizza de calabresa", self.PIZZA)
        alertas = api.estado()["alertas"]
        self.assertEqual([(a["prato"], a["nivel"]) for a in alertas], [("Pizza de calabresa", "nao_da")])
        marcar(equipamentos={"forno": True, "forma": True}, habilidades={"massa_fermentada": "sim", "assar": "sim"})
        self.assertEqual(api.estado()["alertas"], [])  # o aviso vem do estado de agora, não de quando o prato entrou

    def test_ingrediente_avulso_que_nao_tem_como_virar_comida(self):
        marcar(equipamentos={"forno": False})
        out = call("carrinho_adicionar", nome="massa de pizza", preco_total=8)
        self.assertTrue(out["success"])
        self.assertEqual(out["alerta_da_cozinha"]["status"], "nao_da")
        self.assertIn("não tem como fazer pizza com esse ingrediente", out["diga_a_dona_maria"])
        self.assertTrue(out["diga_a_dona_maria"].endswith("saldo R$ 72,00"))
        self.assertEqual([a["item_id"] is not None for a in api.estado()["alertas"]], [True])

    def test_ingrediente_comum_nao_gera_aviso(self):
        out = call("carrinho_adicionar", nome="couve", preco_total=12)
        self.assertNotIn("alerta_da_cozinha", out)
        self.assertEqual(out["diga_a_dona_maria"], "orçamento R$ 80,00 · no carrinho R$ 12,00 · saldo R$ 68,00")

    def test_prato_lembra_de_completar_a_cozinha_antes_de_comprar(self):
        out = call("carrinho_adicionar_prato", prato="Bolo de cenoura", ingredientes=[{"nome": "cenoura", "quantidade": 1, "unidade": "kg", "preco_total": 6}])
        self.assertIn("Ainda não sei tudo da sua cozinha (10 perguntas sem resposta)", out["diga_a_dona_maria"])
        self.assertEqual(len(out["alerta_da_cozinha"]["perfil_pendente"]), 10)
        marcar(**COZINHA_COMPLETA)
        out = call("carrinho_adicionar_prato", prato="Bolo de milho", ingredientes=[{"nome": "milho", "quantidade": 1, "unidade": "kg", "preco_total": 6}])
        self.assertNotIn("Ainda não sei", out["diga_a_dona_maria"])

    def test_analytics_diz_o_que_a_cozinha_faz_e_quanto_cobrar(self):
        marcar(equipamentos={"forno": False})
        out = call("analytics_consultar", escopo="pais", limite=5)
        pizza = out["ranking"][0]
        self.assertEqual((pizza["prato"], pizza["cozinha_dela"]), ("Pizza de calabresa", "nao_da"))
        self.assertIn("forno", pizza["aviso_da_cozinha"])
        qc = pizza["quanto_cobrar"]
        self.assertEqual((qc["custo_tipico_de_exemplo"], qc["cobrar_no_minimo_sem_prejuizo"]), (16.0, 17.78))
        self.assertIn("para_sobrar_30_por_cento", qc)
        self.assertIn("1) Pizza de calabresa (~R$ 52,00; cobrar a partir de R$ 17,78) [NÃO dá na cozinha dela: forno]", out["diga_a_dona_maria"])
        self.assertIn("base de exemplo", out["diga_a_dona_maria"])
        self.assertIn("com o custo real dela o número muda", out["diga_a_dona_maria"])

    def test_receita_de_prato_da_base_soma_o_que_o_modelo_esqueceu(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"forno": False, "forma": True}, habilidades={"massa_fermentada": "sim", "assar": "sim"})
        # o modelo NÃO listou o forno em equipamentos_necessarios: a ficha da pizza lista por ele
        receita = call("receita_conferir_ingredientes", nome="Pizza de calabresa", porcoes=2,
                       ingredientes=[{"nome": "farinha de trigo", "quantidade": 300, "unidade": "g"}, {"nome": "queijo mussarela", "quantidade": 200, "unidade": "g"}])
        self.assertEqual(receita["veredito"], "inviavel_por_enquanto")
        self.assertEqual([b["tipo"] for b in receita["bloqueios"]], ["equipamento_faltando"])
        self.assertEqual(receita["equipamentos"]["faltando"], ["forno"])
        self.assertIn("não dá para fazer Pizza de calabresa", receita["avisos_da_cozinha"][0])
        out = call("prato_calcular_cmv", receita_id=receita["receita_id"])  # e a trava do CMV segura
        self.assertTrue(out["bloqueado"])
        self.assertIn("não dá para fazer Pizza de calabresa", out["diga_a_dona_maria"])

    def test_receita_de_prato_da_base_com_tudo_resolvido_passa(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"forma": True}, habilidades={"massa_fermentada": "sim", "assar": "sim"})
        receita = call("receita_conferir_ingredientes", nome="Pizza de calabresa", porcoes=2,
                       ingredientes=[{"nome": "farinha de trigo", "quantidade": 300, "unidade": "g"}, {"nome": "queijo mussarela", "quantidade": 200, "unidade": "g"}])
        self.assertEqual(receita["veredito"], "viavel", receita["bloqueios"])
        self.assertEqual(receita["avisos_da_cozinha"], [])
        self.assertTrue(call("prato_calcular_cmv", receita_id=receita["receita_id"])["success"])

    def test_ficha_pede_confirmacao_do_que_ela_nao_respondeu(self):
        marcar(**COZINHA_COMPLETA)  # forma e as habilidades da pizza ficaram sem resposta
        receita = call("receita_conferir_ingredientes", nome="Pizza de calabresa", porcoes=2,
                       ingredientes=[{"nome": "farinha de trigo", "quantidade": 300, "unidade": "g"}])
        self.assertEqual(receita["veredito"], "precisa_confirmar")
        tipos = {b["tipo"] for b in receita["bloqueios"]}
        self.assertEqual(tipos, {"equipamento_nao_informado", "tecnica_nao_informada"})
        self.assertIn("Falta você dizer", receita["avisos_da_cozinha"][0])

    def test_nao_repete_o_que_o_modelo_ja_listou(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"forno": False})
        receita = call("receita_conferir_ingredientes", nome="Pizza de calabresa", porcoes=2,
                       ingredientes=[{"nome": "farinha de trigo", "quantidade": 300, "unidade": "g"}], equipamentos_necessarios=["Forno elétrico"])
        self.assertEqual(receita["equipamentos"]["faltando"], ["Forno elétrico"])  # uma vez só, com o nome do modelo


class SalvarComOModeloReal(StoreTestCase):
    """O que a prova com o Qwen3-8B mostrou (15/11/2026): tempo em horas, mensagem inteira em observacoes, técnicas sem salvar."""

    def test_tempo_em_horas_vira_minutos_sozinho(self):
        diz("A geladeira é média, tenho até 2 horas por cozinhada.")
        out = call("perfil_cozinha_salvar", restricoes={"tempo_por_cozinhada_min": 2, "espaco_geladeira": "média"})
        self.assertNotIn("recusados", out)
        self.assertEqual(out["perfil"]["restricoes"], {"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120})  # 2 horas = 120 minutos
        diz("Duas horas e meia", sessao="outra")
        diz("Gosto de cozinhar em 90 minutos, mais ou menos", sessao="outra2")
        self.assertEqual(json.loads(tools.HANDLERS["perfil_cozinha_salvar"]({"restricoes": {"tempo_por_cozinhada_min": 90}}, session_id="outra2"))
                         ["perfil"]["restricoes"]["tempo_por_cozinhada_min"], 90)  # já em minutos: fica como veio

    def test_tempo_pequeno_so_vale_se_ela_disse_a_unidade(self):
        diz("Faço marmita em 10 minutos")
        self.assertEqual(call("perfil_cozinha_salvar", restricoes={"tempo_por_cozinhada_min": 10})["perfil"]["restricoes"]["tempo_por_cozinhada_min"], 10)
        diz("Tenho 3 por semana", sessao="s2")  # o 3 aparece, mas sem unidade: não dá para saber se são horas
        out = json.loads(tools.HANDLERS["perfil_cozinha_salvar"]({"restricoes": {"tempo_por_cozinhada_min": 3}}, session_id="s2"))
        self.assertIn("MINUTOS", out["recusados"][0]["motivo"])
        self.assertIn("corrija o que o motivo diz", out["aviso"])

    def test_unidade_do_numero_na_fala(self):
        dela = lambda texto: falas.extrair([{"role": "user", "content": texto}])
        self.assertEqual(falas.unidade_do_numero(2, dela("tenho até 2 horas")), "h")
        self.assertEqual(falas.unidade_do_numero(2, dela("tenho até duas horas")), "h")
        self.assertEqual(falas.unidade_do_numero(90, dela("uns 90 minutos")), "min")
        self.assertEqual(falas.unidade_do_numero(1.5, dela("1,5 hora")), "h")
        self.assertIsNone(falas.unidade_do_numero(2, dela("2 bocas")))
        self.assertIsNone(falas.unidade_do_numero(2, dela("2 horas ou 2 minutos")))  # ambíguo

    def test_o_modelo_nao_ve_observacoes_no_schema(self):
        self.assertNotIn("observacoes", tools.SCHEMAS["perfil_cozinha_salvar"]["parameters"]["properties"])
        self.assertIn("'tecnicas'", tools.SCHEMAS["perfil_cozinha_salvar"]["description"])

    def test_mensagem_inteira_em_observacoes_e_recusada(self):
        fala = ("Tenho fogão de 4 bocas, forno, panela de pressão e liquidificador. Não tenho air fryer. A geladeira é média, tenho até 2 horas por "
                "cozinhada, o gás é de botijão e a energia é estável. Sei refogar e fazer molho béchamel, mas nunca fiz massa fresca.")
        diz(fala)
        out = call("perfil_cozinha_salvar", observacoes=fala)
        self.assertEqual(out["perfil"]["observacoes"], [])
        self.assertIn("observação longa demais", out["recusados"][0]["motivo"])
        self.assertIn("corrija o que o motivo diz", out["aviso"])
        diz("Só vendo no fim de semana")
        self.assertEqual(call("perfil_cozinha_salvar", observacoes="Só vendo no fim de semana")["perfil"]["observacoes"], ["Só vendo no fim de semana"])

    def test_lembrete_manda_reler_a_mensagem_antes_de_perguntar(self):
        diz("Tenho forno e sei refogar.")
        out = call("perfil_cozinha_salvar", equipamentos={"forno": True})
        self.assertIn("releia o que ela acabou de dizer", out["lembrete"])
        self.assertIn("'tecnicas'", out["lembrete"])
        marcar(**COZINHA_COMPLETA)
        diz("Tenho liquidificador", sessao=SESSAO)
        self.assertNotIn("lembrete", call("perfil_cozinha_salvar", equipamentos={"liquidificador": True}))  # completo: nada a lembrar

    def test_tecnica_dita_com_as_palavras_dela_e_aceita_como_habilidade_do_catalogo(self):
        diz("Sei refogar e fazer molho béchamel, mas nunca fiz massa fresca.")
        out = call("perfil_cozinha_salvar", tecnicas={"refogado": "sim", "molho béchamel": "sim", "massa fresca": "nao"})
        self.assertNotIn("recusados", out, out)
        self.assertEqual(Store().cozinha()["habilidades"], {"refogar": "sim", "molhos": "sim"})  # a tela mostra pelo item do catálogo
        self.assertEqual([o["nome"] for o in Store().cozinha()["outros"]], ["Massa fresca"])

    def test_valor_da_rotina_sem_acento_casa_com_a_opcao_da_tela(self):
        for dito, esperado in (("media", "médio"), ("médio", "médio"), ("pouco", "pouco"), ("Bastante espaço", "bastante")):
            self.assertEqual(cozinha.rotina_canonica("espaco_geladeira", dito), esperado, dito)
        self.assertEqual([cozinha.rotina_canonica("gas", v) for v in ("botijao", "botijão ok", "encanado", "falta as vezes")],
                         ["botijão", "botijão", "encanado", "falta às vezes"])
        self.assertEqual([cozinha.rotina_canonica("energia", v) for v in ("estavel", "cai de vez em quando")], ["estável", "cai às vezes"])
        self.assertEqual(cozinha.rotina_canonica("tempo_por_cozinhada_min", 120), 120)
        self.assertEqual(cozinha.rotina_canonica("gas", "de rua"), "de rua")  # não casou: fica como veio
        diz("A geladeira é média, o gás é de botijão e a energia é estável.")
        out = call("perfil_cozinha_salvar", restricoes={"espaco_geladeira": "media", "gas": "botijao", "energia": "estavel"})
        self.assertEqual(out["perfil"]["restricoes"], {"espaco_geladeira": "médio", "gas": "botijão", "energia": "estável"})
        Store().perfil_salvar({"restricoes": {"espaco_geladeira": "média"}})  # o que já estava gravado sem acento também aparece certo na tela
        self.assertEqual(Store().cozinha()["rotina"]["espaco_geladeira"], "médio")

    def test_depois_de_salvar_a_consultora_recebe_o_resumo_pronto(self):
        diz("Tenho fogão de 4 bocas e forno. Não tenho air fryer. Sei refogar.")
        out = call("perfil_cozinha_salvar", equipamentos={"fogão": 4, "forno": True, "air fryer": False}, tecnicas={"refogado": "sim"})
        self.assertEqual(out["diga_a_dona_maria"],
                         "Salvei na sua cozinha. Você tem: fogão (4 bocas), forno; Não tem: air fryer; Sabe: refogar e temperar. "
                         "Você vê e corrige tudo em Minha cozinha (menu). Ainda falta saber: equipamentos e utensílios, rotina da cozinha.")
        self.assertIn("3. **Rotina da cozinha:**", out["perguntas_prontas"].replace("2. **Rotina", "3. **Rotina"))  # só o que falta
        self.assertNotIn("Técnicas que você domina", out["perguntas_prontas"])
        marcar(**COZINHA_COMPLETA)
        diz("Tenho liquidificador", sessao=SESSAO)
        completo = call("perfil_cozinha_salvar", equipamentos={"liquidificador": True})
        self.assertTrue(completo["diga_a_dona_maria"].endswith("A cozinha está completa."))
        self.assertNotIn("perguntas_prontas", completo)
        self.assertEqual(call("perfil_cozinha_salvar")["salvo"], {})  # nada salvo, nada a repetir
        self.assertNotIn("diga_a_dona_maria", call("perfil_cozinha_salvar"))


class PainelDaCozinhaTests(StoreTestCase):
    def test_primeira_mensagem_manda_perguntar_ja_na_primeira_resposta(self):
        texto = painel.texto_do_painel(primeira_mensagem=True)
        self.assertIn("COZINHA INCOMPLETA E ESTA É A PRIMEIRA MENSAGEM DA CONVERSA", texto)
        self.assertIn("faça JÁ NESTA RESPOSTA as perguntas abaixo", texto)
        for pergunta in ("quantas bocas", "forno", "panela de pressão", "air fryer", "liquidificador", "molho béchamel", "ponto da carne",
                         "geladeira", "gás", "energia", "cozinhada"):  # o que o desafio manda descobrir
            self.assertIn(pergunta, texto)
        self.assertIn("ainda não marcou nada em Minha cozinha", texto)

    def test_hook_usa_o_is_first_turn_do_hermes(self):
        primeiro = painel.ao_iniciar_turno(session_id="s", user_message="oi", conversation_history=[], is_first_turn=True)["context"]
        self.assertIn("PRIMEIRA MENSAGEM DA CONVERSA", primeiro)
        depois = painel.ao_iniciar_turno(session_id="s", user_message="oi", conversation_history=[], is_first_turn=False)["context"]
        self.assertNotIn("PRIMEIRA MENSAGEM", depois)
        self.assertIn("COZINHA INCOMPLETA (10 perguntas sem resposta)", depois)
        self.assertIn("releia a última mensagem dela", depois)  # o 8B esquece que ela já respondeu: manda reler antes de perguntar

    def test_so_pergunta_o_que_ainda_falta(self):
        marcar(equipamentos={"fogao": 4, "forno": True, "panela_pressao": True, "air_fryer": False, "liquidificador": True})
        texto = painel.texto_do_painel(primeira_mensagem=True)
        self.assertNotIn("quantas bocas", texto)
        self.assertNotIn("1. **Equipamentos e utensílios:**", texto)
        self.assertIn("1. **Técnicas que você domina:**", texto)
        self.assertIn("2. **Rotina da cozinha:**", texto)
        self.assertIn("TEM: fogão (4 bocas), forno, liquidificador, panela de pressão; NÃO TEM: air fryer", texto)

    def test_cozinha_completa_nao_pergunta_de_novo(self):
        marcar(**COZINHA_COMPLETA)
        for primeira in (True, False):
            texto = painel.texto_do_painel(primeira_mensagem=primeira)
            self.assertIn("Cozinha completa: não pergunte de novo", texto)
            self.assertNotIn("PERGUNTAS DA COZINHA", texto)
            self.assertNotIn("COZINHA INCOMPLETA", texto)

    def test_o_que_foi_salvo_aparece_para_a_consultora(self):
        marcar(**COZINHA_COMPLETA)
        marcar(habilidades={"assar": "aprendendo", "sushi": "nao"})
        texto = painel.texto_do_painel()
        self.assertIn("SABE: refogar e temperar", texto)
        self.assertIn("está APRENDENDO: assar no forno", texto)
        self.assertIn("NÃO SABE: fazer sushi e preparar peixe cru", texto)
        self.assertIn("rotina: espaço na geladeira médio, tempo por cozinhada até 2 horas, gás botijão, energia elétrica estável", texto)

    def test_mais_vendidos_marca_o_que_a_cozinha_nao_faz(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"forno": False})
        self.assertIn("1) Pizza de calabresa (~R$ 52,00) [NÃO dá na cozinha dela: forno]", painel.texto_do_painel())

    def test_as_perguntas_do_painel_nao_viram_fala_dela(self):
        from sabor_da_maria import falas
        mensagem = "Quero montar o cardápio\n\n" + painel.texto_do_painel(primeira_mensagem=True)
        lista = falas.extrair([{"role": "user", "content": mensagem}])
        self.assertEqual([f.texto for f in lista], ["Quero montar o cardápio"])
        self.assertFalse(falas.apoia(["air fryer"], lista))  # a pergunta do painel cita air fryer; ela não

    def test_painel_nunca_derruba_o_turno_sem_banco(self):
        import os
        log = logging.getLogger("sabor_da_maria.painel")
        nivel = log.level
        log.setLevel(logging.CRITICAL)
        self.addCleanup(log.setLevel, nivel)
        os.environ["SABOR_DA_MARIA_DB"] = "/proc/nao-existe/sabor.db"
        self.assertIsNone(painel.ao_iniciar_turno(is_first_turn=True))


class ApiDaCozinhaTests(StoreTestCase):
    def test_estado_traz_a_cozinha_as_opcoes_e_os_avisos(self):
        st, estado = api.tratar("GET", "/api/estado")
        self.assertEqual(st, 200)
        self.assertEqual(estado["cozinha"]["resumo"]["respondidos"], 0)
        self.assertEqual(estado["alertas"], [])
        self.assertEqual(len(estado["opcoes"]["cozinha"]["habilidades"]), 15)
        self.assertIn("tipos", estado["opcoes"])  # as opções do negócio continuam lá

    def test_put_grava_e_devolve_o_estado(self):
        st, estado = api.tratar("PUT", "/api/cozinha", COZINHA_COMPLETA)
        self.assertEqual(st, 200)
        self.assertEqual(estado["cozinha"]["equipamentos"]["fogao"], 4)
        self.assertEqual(estado["cozinha"]["habilidades"], {"refogar": "sim"})
        self.assertEqual(estado["cozinha"]["rotina"]["gas"], "botijão")
        self.assertEqual(Store().perfil()["equipamentos"]["forno"], True)

    def test_put_recusa_o_que_nao_e_do_catalogo(self):
        st, resposta = api.tratar("PUT", "/api/cozinha", {"equipamentos": {"geladeirinha": True}})
        self.assertEqual(st, 400)
        self.assertIn("não está na lista", resposta["erro"])
        self.assertEqual(api.tratar("PUT", "/api/cozinha", [1])[0], 400)
        self.assertEqual(api.tratar("PATCH", "/api/cozinha", {})[0], 405)

    def test_analytics_vem_com_cozinha_e_preco(self):
        api.tratar("PUT", "/api/cozinha", {"equipamentos": {"forno": False}})
        st, r = api.tratar("GET", "/api/analytics?escopo=pais&limite=3")
        self.assertEqual(st, 200)
        pizza = r["ranking"][0]
        self.assertEqual((pizza["cozinha"]["status"], pizza["preco"]["preco_minimo"]), ("nao_da", 17.78))
        self.assertEqual(pizza["preco"]["mercado"]["recebe_liquido"], 46.8)

    def test_precos_para_a_calculadora_da_tela(self):
        st, q = api.tratar("GET", "/api/precos?cmv=9,50&preco=29.40")
        self.assertEqual((st, q["preco_minimo"], q["mercado"]["lucro"]), (200, 10.56, 16.96))
        self.assertEqual(api.tratar("GET", "/api/precos?cmv=9.5")[1]["mercado"], None)
        for ruim in ("/api/precos", "/api/precos?cmv=0", "/api/precos?cmv=abc", "/api/precos?cmv=9.5&preco=x"):
            self.assertEqual(api.tratar("GET", ruim)[0], 400, ruim)
        self.assertEqual(api.tratar("POST", "/api/precos", {})[0], 405)


if __name__ == "__main__":
    unittest.main()
