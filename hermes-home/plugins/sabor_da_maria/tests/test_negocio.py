"""Meu negócio (onde a loja fica, tipo de negócio e de alimentação) contado na conversa e os pratos em Minha cozinha."""

import json
import os
import unittest

from sabor_da_maria import analytics, api, cozinha, falas, negocio, painel, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase, diz
from .test_cozinha import COZINHA_COMPLETA, NEGOCIO_COMPLETO, call, marcar


def salvar(**args):
    return call("negocio_salvar", **args)


class CatalogoDoNegocioTests(unittest.TestCase):
    def test_as_categorias_do_tipo_de_alimentacao_sao_as_dos_pratos_da_base(self):
        """A ponte negócio -> Minha cozinha: cada tipo de alimentação aponta para uma categoria da base e nenhuma categoria fica sem tipo."""
        da_base = {f.categoria for f in cozinha.carregar().pratos.values()}
        dos_tipos = {categoria for _, categoria in negocio.ALIMENTACAO.values()}
        self.assertEqual(da_base, dos_tipos)
        self.assertEqual(len(dos_tipos), len(negocio.ALIMENTACAO))  # uma categoria por tipo

    def test_o_que_sai_do_negocio_nao_esta_mais_nas_opcoes(self):
        o = negocio.opcoes()
        self.assertEqual(set(o), {"tipos", "alimentacao", "paises", "estados", "zonas"})
        self.assertEqual(o["paises"], [{"id": "Brasil", "label": "Brasil"}])
        n = negocio.normalizar({"tipo": "pizzaria", "vende": ["pizzas"], "publico": "x", "observacoes": "y"})
        self.assertEqual(set(n), {"tipo", "alimentacao", "pais", "estado", "cidade", "zona"})  # sem vende, publico nem observacoes

    def test_do_que_ela_diz_para_o_catalogo(self):
        self.assertEqual([negocio.resolver_tipo(x) for x in ("Restaurante de hotel", "restaurante_hotel", "marmitaria", "Pizzaria", "banca")],
                         ["restaurante_hotel", "restaurante_hotel", "marmitaria", "pizzaria", None])
        self.assertEqual([negocio.resolver_alimentacao(x) for x in ("Pizzas", "marmitas", "comida caseira e prato do dia", "doce", "sushi", "xis")],
                         ["pizza", "marmita", "caseira", "doces", "japonesa", None])
        self.assertEqual([negocio.resolver_zona(x) for x in ("Zona Leste", "leste da cidade", "centro", "leste", "nordeste")],
                         ["leste", "leste", "centro", "leste", None])
        self.assertEqual([negocio.resolver_estado(x) for x in ("sp", "São Paulo", "sao paulo zona leste", "Rio Grande do Sul", "rio de janeiro", "Pará", "Marte")],
                         ["SP", "SP", "SP", "RS", "RJ", "PA", None])

    def test_o_que_falta_perguntar(self):
        self.assertEqual([p["campo"] for p in negocio.pendencias(None)], ["local", "tipo", "alimentacao"])
        self.assertEqual(negocio.pendencias(NEGOCIO_COMPLETO), [])
        so_estado = {**NEGOCIO_COMPLETO, "cidade": ""}
        self.assertEqual([p["campo"] for p in negocio.pendencias(so_estado)], ["local"])  # estado sem cidade não basta
        self.assertEqual(negocio.pendencias({**NEGOCIO_COMPLETO, "zona": ""}), [])  # a zona é pedida junto, mas nem toda cidade tem
        self.assertEqual([t for t, _ in negocio.blocos_de_perguntas(negocio.pendencias(None))],
                         ["Onde fica a loja", "Tipo de negócio", "Tipo de alimentação que você faz"])

    def test_descricoes_para_a_consultora_e_para_ela(self):
        self.assertIn("ainda não preencheu", negocio.descrever(None))
        self.assertIn("nem contou na conversa", negocio.descrever({}))
        completo = {**NEGOCIO_COMPLETO, "alimentacao": ["marmita", "pizza"]}
        self.assertEqual(negocio.descrever(completo),
                         "Marmitaria. faz: Marmitas; Pizzas. local: Zona Leste, São Paulo/SP (região Sudeste).")
        self.assertEqual(negocio.resumo_para_ela(completo),
                         "É um negócio do tipo marmitaria; faz marmitas, pizzas; fica em Zona Leste, São Paulo/SP (região Sudeste).")
        self.assertIn("tipo de alimentação não informado", negocio.descrever({"tipo": "padaria"}))


class FalasDoNegocioTests(unittest.TestCase):
    def dela(self, *textos):
        return falas.extrair([{"role": "user", "content": t} for t in textos])

    def test_frase_inteira_nao_so_as_palavras(self):
        self.assertTrue(falas.contem_frase(["zona leste"], self.dela("minha loja é na zona leste")))
        self.assertFalse(falas.contem_frase(["zona sul"], self.dela("Rio Grande do Sul, zona norte")))  # "zona" e "sul" soltas
        self.assertTrue(falas.contem_frase(["zona sul"], self.dela("Fica na ZONA SUL!")))  # sem acento, caixa e pontuação

    def test_sigla_so_em_maiusculas_e_sozinha(self):
        self.assertTrue(falas.cita_sigla("SP", self.dela("Moro em Campinas, SP")))
        self.assertFalse(falas.cita_sigla("PA", self.dela("pa lá, pa cá")))  # "pa" é palavra
        self.assertFalse(falas.cita_sigla("SP", self.dela("ESPECIAL")))
        self.assertFalse(falas.cita_sigla("MA", self.dela("MARIA")))


class NegocioPelaConversaTests(StoreTestCase):
    def test_o_que_ela_disse_entra_e_aparece_na_tela(self):
        diz("Minha loja está em São Paulo, zona leste. É uma marmitaria e eu faço marmita e pizza.")
        out = salvar(estado="São Paulo", cidade="São Paulo", zona="leste", tipo="marmitaria", alimentacao=["marmita", "pizza"])
        self.assertTrue(out["success"], out)
        self.assertNotIn("recusados", out)
        n = Store().negocio()
        self.assertEqual((n["estado"], n["cidade"], n["zona"], n["tipo"], n["alimentacao"], n["pais"]), ("SP", "São Paulo", "leste", "marmitaria", ["marmita", "pizza"], "Brasil"))
        self.assertEqual(api.estado()["negocio"]["alimentacao"], ["marmita", "pizza"])  # a tela de Meu negócio lê o mesmo registro
        self.assertTrue(out["completo"])
        self.assertEqual(out["diga_a_dona_maria"], "Salvei o seu negócio. É um negócio do tipo marmitaria; faz marmitas, pizzas; fica em Zona Leste, "
                                                   "São Paulo/SP (região Sudeste). Você vê e corrige tudo em Meu negócio (menu). O negócio está completo.")
        self.assertNotIn("perguntas_prontas", out)

    def test_o_que_ela_nao_disse_nao_entra(self):
        diz("Tenho uma loja em Campinas, interior de SP.")
        out = salvar(estado="SP", cidade="Campinas", zona="sul", tipo="pizzaria", alimentacao=["japonesa", "doces"])
        self.assertEqual(out["salvo"], {"estado": "SP", "cidade": "Campinas"})
        motivos = {(r["grupo"], r["valor"]): r["motivo"] for r in out["recusados"]}
        self.assertEqual(set(motivos), {("zona", "sul"), ("tipo", "pizzaria"), ("alimentacao", "japonesa"), ("alimentacao", "doces")})
        self.assertIn("NÃO foram salvos", out["aviso"])
        self.assertEqual(Store().negocio()["tipo"], "")
        self.assertFalse(out["completo"])
        self.assertEqual([p["campo"] for p in out["pendencias"]], ["tipo", "alimentacao"])
        self.assertIn("2. **Tipo de alimentação que você faz:**", out["perguntas_prontas"])  # só o que falta, já pronto
        self.assertNotIn("Onde fica a loja", out["perguntas_prontas"])

    def test_sem_a_fala_dela_nada_entra(self):
        out = salvar(estado="SP", cidade="São Paulo")
        self.assertTrue(out["bloqueado"])
        self.assertFalse(out["success"])
        self.assertIn("ainda não tenho a fala dela", out["recusados"][0]["motivo"])
        self.assertIsNone(Store().negocio())

    def test_a_palavra_solta_nao_vira_zona(self):
        diz("Minha loja é no Rio Grande do Sul, em Porto Alegre, na zona norte.")
        out = salvar(estado="RS", cidade="Porto Alegre", zona="sul")
        self.assertEqual(out["salvo"], {"estado": "RS", "cidade": "Porto Alegre"})
        self.assertEqual(out["recusados"][0]["grupo"], "zona")
        self.assertEqual(salvar(zona="norte")["salvo"], {"zona": "norte"})

    def test_resposta_curta_a_pergunta_da_zona(self):
        falas.registrar(SESSAO, [{"role": "assistant", "content": "E em que zona da cidade fica a sua loja?"}, {"role": "user", "content": "leste"}])
        self.assertEqual(salvar(zona="leste")["salvo"], {"zona": "leste"})
        self.assertIn("recusados", salvar(zona="oeste"))  # só a que ela escolheu

    def test_sigla_vale_se_ela_a_escreveu(self):
        diz("Fica em BH, MG.")
        out = salvar(estado="MG", cidade="Belo Horizonte")
        self.assertEqual(out["salvo"], {"estado": "MG"})  # a sigla vale; "Belo Horizonte" ela não escreveu
        self.assertEqual(out["recusados"][0]["grupo"], "cidade")

    def test_nome_fora_da_lista_volta_para_o_modelo_corrigir(self):
        diz("Sou uma banca de pastel e faço sushi.")
        out = salvar(tipo="banca", alimentacao=["sushi", "xis"], estado="Marte")
        self.assertEqual(out["salvo"], {"alimentacao": ["japonesa"]})  # "sushi" casa com a comida japonesa e ela disse
        self.assertEqual({r["valor"] for r in out["recusados"]}, {"banca", "xis", "Marte"})
        self.assertTrue(all("da lista" in r["motivo"] for r in out["recusados"]))

    def test_tipo_outro_nao_pede_evidencia(self):
        diz("Vendo comida em casa.")
        self.assertEqual(salvar(tipo="outro")["salvo"], {"tipo": "outro"})

    def test_o_que_ela_vai_contando_se_soma(self):
        diz("Faço marmitas.")
        salvar(alimentacao=["marmita"])
        diz("E também pizza e doces.", sessao=SESSAO)
        salvo = salvar(alimentacao=["pizza", "doces"])["negocio"]
        self.assertEqual(salvo["alimentacao"], ["marmita", "pizza", "doces"])

    def test_a_tool_do_negocio_so_pergunta_o_que_e_do_negocio(self):
        diz("Minha loja é em Recife, Pernambuco.")
        out = salvar(estado="PE", cidade="Recife")
        self.assertIn("Sobre o negócio", out["perguntas_prontas"])
        self.assertNotIn("Sobre a cozinha", out["perguntas_prontas"])  # a cozinha é da outra tool (e do painel, que junta os dois)

    def test_conferencia_de_falas_pode_ser_desligada_para_depurar(self):
        os.environ["SABOR_DA_MARIA_CONFERIR_FALAS"] = "0"
        self.addCleanup(os.environ.pop, "SABOR_DA_MARIA_CONFERIR_FALAS", None)
        self.assertEqual(salvar(tipo="pizzaria")["salvo"], {"tipo": "pizzaria"})

    def test_o_modelo_ve_enums_fechados_no_schema(self):
        props = tools.SCHEMAS["negocio_salvar"]["parameters"]["properties"]
        self.assertEqual(set(props), {"estado", "cidade", "zona", "tipo", "alimentacao"})
        self.assertEqual(props["zona"]["enum"], list(negocio.ZONAS))
        self.assertEqual(props["alimentacao"]["items"]["enum"], list(negocio.ALIMENTACAO))
        self.assertEqual(set(tools.SCHEMAS), set(tools.HANDLERS))
        self.assertIn("despensa_registrar_compras", tools.SCHEMAS)


class PainelDoNegocioTests(StoreTestCase):
    def test_primeira_mensagem_traz_as_perguntas_do_negocio_e_da_cozinha_numeradas(self):
        texto = painel.texto_do_painel(primeira_mensagem=True)
        self.assertIn("preciso conhecer o seu negócio e a sua cozinha. Me conta:", texto)
        self.assertIn("**Sobre o negócio**\n1. **Onde fica a loja:**", texto)
        self.assertIn("**Sobre a cozinha**\n4. **Equipamentos e utensílios:**", texto)
        self.assertIn("marque em **Meu negócio** e **Minha cozinha** (menu)", texto)
        self.assertIn("Negócio: A Dona Maria ainda não preencheu o perfil do negócio", texto)

    def test_so_pergunta_o_negocio_quando_a_cozinha_ja_esta_completa(self):
        marcar(**COZINHA_COMPLETA)
        texto = painel.texto_do_painel(primeira_mensagem=True)
        self.assertIn("NEGÓCIO INCOMPLETO E ESTA É A PRIMEIRA MENSAGEM DA CONVERSA", texto)
        self.assertIn("Cozinha completa: não pergunte de novo", texto)
        self.assertNotIn("Nenhum prato é aceito antes", texto)  # o negócio não trava o aceite do prato
        self.assertIn("preciso conhecer o seu negócio. Me conta:", texto)
        self.assertNotIn("Sobre a cozinha", texto)

    def test_o_que_o_modelo_salvou_nao_e_perguntado_de_novo(self):
        diz("Minha loja é em Salvador, Bahia, e faço comida caseira.")
        salvar(estado="BA", cidade="Salvador", alimentacao=["caseira"])
        texto = painel.texto_do_painel(primeira_mensagem=True)
        self.assertNotIn("Onde fica a loja", texto)
        self.assertNotIn("Tipo de alimentação que você faz", texto)
        self.assertIn("1. **Tipo de negócio:**", texto)
        self.assertIn("faz: Comida caseira e prato do dia", texto)

    def test_o_que_foi_salvo_nao_vira_fala_dela(self):
        mensagem = "Oi\n\n" + painel.texto_do_painel(primeira_mensagem=True)
        lista = falas.extrair([{"role": "user", "content": mensagem}])
        self.assertEqual([f.texto for f in lista], ["Oi"])
        self.assertFalse(falas.apoia(["marmitaria"], lista))  # a pergunta do painel cita marmitaria; ela não


class PratosDaCozinhaTests(StoreTestCase):
    def test_cada_prato_com_o_que_precisa_e_o_que_ela_tem(self):
        marcar(equipamentos={"fogao": 4, "forno": True, "forma": False, "frigideira": True, "panela_pressao": False, "panela_grande": True},
               habilidades={"assar": "aprendendo", "grelhar": "sim"})
        r = cozinha.pratos_da_cozinha(Store().perfil())
        self.assertEqual(r["resumo"]["total"], 47)
        self.assertEqual(sum(r["resumo"][k] for k in ("ok", "atencao", "confirmar", "nao_da")), 47)
        pratos = {p["prato"]: p for c in r["categorias"] for p in c["pratos"]}
        pizza = pratos["Pizza de calabresa"]
        self.assertEqual(pizza["status"], "nao_da")
        self.assertEqual([(x["nome"], x["estado"]) for x in pizza["precisa"]], [("forno", "ok"), ("forma", "falta")])
        self.assertEqual(pizza["precisa"][0]["com"], "forno")
        self.assertEqual([(h["nome"], h["nivel"]) for h in pizza["habilidades"]],
                         [("Massas com fermento (pizza e pães)", None), ("Assar no forno", "aprendendo")])
        self.assertIn("não dá para fazer Pizza de calabresa", pizza["mensagem"])
        feijoada = pratos["Feijoada completa"]  # alternativas: basta a panela grande
        self.assertEqual([(x["nome"], x["estado"], x["com"]) for x in feijoada["precisa"]],
                         [("fogão", "ok", "fogão"), ("panela de pressão ou panela grande", "ok", "panela grande")])
        self.assertEqual(pratos["Sanduíche de mortadela"]["precisa"], [])

    def test_as_categorias_dela_vem_primeiro_e_o_que_da_para_fazer_antes(self):
        marcar(**COZINHA_COMPLETA)
        marcar(equipamentos={"frigideira": True, "panela_grande": True, "forma": True}, habilidades={"arroz_feijao": "sim", "grelhar": "sim", "fritar": "sim"})
        sem_loja = cozinha.pratos_da_cozinha(Store().perfil())
        self.assertEqual(sem_loja["categorias"][0]["nome"], "Japonesa")  # a ordem da base
        com_loja = cozinha.pratos_da_cozinha(Store().perfil(), negocio.categorias_da_loja({"alimentacao": ["pizza", "caseira"]}))
        self.assertEqual([(c["nome"], c["da_loja"]) for c in com_loja["categorias"]][:3], [("Pizza", True), ("Prato do dia", True), ("Japonesa", False)])
        do_dia = next(c for c in com_loja["categorias"] if c["nome"] == "Prato do dia")["pratos"]
        ordem = {"ok": 0, "atencao": 1, "confirmar": 2, "nao_da": 3}
        self.assertEqual([ordem[p["status"]] for p in do_dia], sorted(ordem[p["status"]] for p in do_dia))  # o que dá para fazer vem antes
        self.assertEqual(do_dia[0]["status"], "ok")

    def test_sem_nada_marcado_tudo_a_confirmar(self):
        r = cozinha.pratos_da_cozinha({})
        self.assertEqual((r["resumo"]["confirmar"], r["resumo"]["ok"]), (47, 0))  # até o sanduíche, que não pede equipamento, pede uma habilidade
        self.assertEqual(r["resumo"]["nao_da"] + r["resumo"]["atencao"], 0)

    def test_rota_da_tela(self):
        api.tratar("PUT", "/api/negocio", {"alimentacao": ["pizza"], "estado": "SP", "cidade": "São Paulo"})
        api.tratar("PUT", "/api/cozinha", {"equipamentos": {"forno": False}})
        status, r = api.tratar("GET", "/api/cozinha/pratos")
        self.assertEqual(status, 200)
        self.assertEqual(r["categorias"][0]["nome"], "Pizza")
        self.assertTrue(r["categorias"][0]["da_loja"])
        self.assertEqual(r["categorias"][0]["pratos"][0]["status"], "nao_da")
        self.assertEqual(api.tratar("POST", "/api/cozinha/pratos", {})[0], 405)
        self.assertEqual(api.tratar("GET", "/api/cozinha/outra")[0], 405)


if __name__ == "__main__":
    unittest.main()
