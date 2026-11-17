"""Analytics: pratos mais vendidos priorizados pelo lugar da loja, a base do repositório e a localização no perfil do negócio."""

import json
import tempfile
import unittest
from pathlib import Path

from sabor_da_maria import analytics, api, negocio, painel, tools
from sabor_da_maria.store import Store
from sabor_da_maria.units import norm_text

from ._base import SESSAO, StoreTestCase


def call(name, **args):
    handler = tools.analytics_consultar if name == "analytics_consultar" else tools.HANDLERS[name]
    return json.loads(handler(args, session_id=SESSAO))


CABECALHO = "| # | Prato | Categoria | Preço médio (R$) | Faixa (R$) | Índice de vendas | Ingredientes típicos |\n|---|---|---|---|---|---|---|\n"


def arquivo(escopo, regiao="", estado="", secoes=""):
    meta = f"---\nescopo: {escopo}\npais: Brasil\n" + (f"regiao: {regiao}\n" if regiao else "") + (f"estado: {estado}\n" if estado else "") + "fonte: teste\n---\n"
    return meta + "# t\n\n> Dados ilustrativos.\n\n" + secoes


def tabela(*linhas):
    corpo = "".join(f"| {i} | {n} | {c} | {p} | {f} | {x} | {ing} |\n" for i, (n, c, p, f, x, ing) in enumerate(linhas, 1))
    return CABECALHO + corpo


A = ("Prato A", "prato do dia", "30,00", "25–36", 100, "arroz, feijão")
B = ("Prato B", "lanche", "20,00", "17–24", 90, "pão")
C = ("Prato C", "sobremesa", "12,50", "10–15", 100, "leite")
D = ("Prato D", "lanche", "15,00", "12–18", 60, "queijo")


def base_de_teste(pasta: Path) -> Path:
    raiz = pasta / "pratos-mais-vendidos"
    (raiz / "sudeste").mkdir(parents=True)
    (raiz / "brasil.md").write_text(arquivo("pais", secoes="## País\n" + tabela(A, B, (C[0], *C[1:4], 10, C[5]))), encoding="utf-8")
    (raiz / "sudeste" / "_regiao.md").write_text(arquivo("regiao", "Sudeste", secoes="## Região\n" + tabela(A, (B[0], *B[1:4], 80, B[5]))), encoding="utf-8")
    (raiz / "sudeste" / "sao-paulo.md").write_text(arquivo(
        "estado", "Sudeste", "SP",
        "## Estado\nLeitura do estado.\n\n" + tabela(A, (B[0], *B[1:4], 70, B[5]))
        + "\n## Cidade: Santos\nLeitura da cidade.\n\n" + tabela((B[0], *B[1:4], 100, B[5]), (A[0], *A[1:4], 60, A[5]))
        + "\n### Zona Sul\nLeitura da zona.\n\n" + tabela(C, D)), encoding="utf-8")
    return raiz


LOJA_ZONA = {"estado": "SP", "cidade": "Santos", "zona": "sul"}


class LeitorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = base_de_teste(Path(self._tmp.name))

    def test_le_estado_cidade_e_zona_do_mesmo_arquivo(self):
        secoes = analytics.analisar((self.base / "sudeste" / "sao-paulo.md").read_text(encoding="utf-8"), "sao-paulo.md")
        self.assertEqual([(s.escopo, s.cidade, s.zona) for s in secoes], [("estado", "", ""), ("cidade", "Santos", ""), ("zona", "Santos", "Zona Sul")])
        self.assertEqual(secoes[0].leitura, "Leitura do estado.")
        prato = secoes[0].pratos[0]
        self.assertEqual((prato.nome, prato.preco_medio, prato.preco_min, prato.preco_max, prato.indice, prato.ingredientes),
                         ("Prato A", 30.0, 25.0, 36.0, 100, "arroz, feijão"))
        self.assertEqual(len(analytics.carregar(self.base)), 5)

    def test_tabela_fora_do_formato_diz_o_arquivo_e_a_linha(self):
        ruins = {
            "colunas": "## Estado\n| # | Prato |\n|---|---|\n| 1 | só duas |\n",
            "indice": "## Estado\n" + tabela(("Prato", "x", "10,00", "8–12", 120, "a")),
            "faixa": "## Estado\n" + tabela(("Prato", "x", "10,00", "12–8", 50, "a")),
        }
        for nome, secoes in ruins.items():
            with self.assertRaises(ValueError, msg=nome) as ctx:
                analytics.analisar(arquivo("estado", "Sudeste", "SP", secoes), "ruim.md")
            self.assertIn("ruim.md, linha", str(ctx.exception))

    def test_arquivo_com_defeito_nao_derruba_os_outros(self):
        (self.base / "sudeste" / "defeito.md").write_text(arquivo("estado", "Sudeste", "RJ", "## Estado\n| # | Prato |\n|---|---|\n| 1 | x |\n"), encoding="utf-8")
        with self.assertLogs("sabor_da_maria.analytics", level="WARNING"):
            secoes = analytics.carregar(self.base)
        self.assertEqual(len(secoes), 5)

    def test_edicao_da_base_vale_sem_reiniciar(self):
        antes = analytics.consultar(None, "pais", 10, pasta=self.base)["ranking"][0]["prato"]
        self.assertEqual(antes, "Prato A")
        (self.base / "brasil.md").write_text(arquivo("pais", secoes="## País\n" + tabela(D, A)), encoding="utf-8")
        self.assertEqual(analytics.consultar(None, "pais", 10, pasta=self.base)["ranking"][0]["prato"], "Prato D")


class PrioridadeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = base_de_teste(Path(self._tmp.name))

    def test_o_lugar_mais_proximo_pesa_mais_que_todos_os_amplos(self):
        r = analytics.consultar(LOJA_ZONA, "prioridade", 10, pasta=self.base)
        self.assertTrue(r["local_definido"])
        self.assertEqual([m["prato"] for m in r["ranking"]], ["Prato C", "Prato B", "Prato A", "Prato D"])
        self.assertEqual([e["escopo"] for e in r["escopos_disponiveis"]], ["zona", "cidade", "estado", "regiao", "pais"])
        c = r["ranking"][0]
        self.assertEqual((c["posicao"], c["onde"][0]), (1, {"escopo": "zona", "posicao": 1, "indice": 100}))
        self.assertEqual(c["preco_medio"], 12.5)  # preço do escopo mais local onde o prato aparece
        self.assertEqual(r["ranking"][1]["preco_medio"], 20.0)
        self.assertIn("exemplo", r["aviso"])
        self.assertEqual(sum(analytics.PESOS[e] for e in ("cidade", "estado", "regiao", "pais")) < analytics.PESOS["zona"], True)

    def test_loja_so_com_cidade_ou_so_com_estado(self):
        r = analytics.consultar({"estado": "SP", "cidade": "Santos"}, "prioridade", 10, pasta=self.base)
        self.assertEqual([e["escopo"] for e in r["escopos_disponiveis"]], ["cidade", "estado", "regiao", "pais"])
        self.assertEqual(r["ranking"][0]["prato"], "Prato B")  # lidera a cidade
        r = analytics.consultar({"estado": "SP"}, "prioridade", 10, pasta=self.base)
        self.assertEqual([e["escopo"] for e in r["escopos_disponiveis"]], ["estado", "regiao", "pais"])
        self.assertEqual(r["ranking"][0]["prato"], "Prato A")

    def test_sem_localizacao_vale_so_o_pais(self):
        r = analytics.consultar(None, "prioridade", 10, pasta=self.base)
        self.assertFalse(r["local_definido"])
        self.assertEqual([m["prato"] for m in r["ranking"]], ["Prato A", "Prato B", "Prato C"])
        self.assertIn("Meu negócio", r["mensagem"])

    def test_escopo_unico_categoria_e_limite(self):
        r = analytics.consultar(LOJA_ZONA, "cidade", 10, pasta=self.base)
        self.assertEqual([(m["posicao"], m["prato"], m["indice"]) for m in r["ranking"]], [(1, "Prato B", 100), (2, "Prato A", 60)])
        self.assertEqual(r["leitura"], "Leitura da cidade.")
        r = analytics.consultar(LOJA_ZONA, "prioridade", 10, categoria="lanche", pasta=self.base)
        self.assertEqual({m["categoria"] for m in r["ranking"]}, {"lanche"})
        self.assertEqual(len(analytics.consultar(LOJA_ZONA, "prioridade", 2, pasta=self.base)["ranking"]), 2)
        r = analytics.consultar({"estado": "RJ"}, "zona", 10, pasta=self.base)
        self.assertEqual(r["ranking"], [])
        self.assertIn("zona", r["mensagem"])
        with self.assertRaises(ValueError):
            analytics.consultar(LOJA_ZONA, "bairro", 10, pasta=self.base)
        self.assertEqual(analytics.consultar(LOJA_ZONA, "região", 10, pasta=self.base)["escopo"], "regiao")

    def test_base_ausente(self):
        with tempfile.TemporaryDirectory() as vazio:
            r = analytics.consultar(LOJA_ZONA, "prioridade", 10, pasta=Path(vazio))
        self.assertEqual(r["ranking"], [])


class BaseDoRepositorioTests(unittest.TestCase):
    """A base de exemplo em prompts/base-de-conhecimento/pratos-mais-vendidos/ precisa estar sempre no formato que o leitor entende."""

    @classmethod
    def setUpClass(cls):
        cls.raiz = analytics.raiz()

    def test_existe_e_esta_organizada_por_regiao_e_estado(self):
        self.assertIsNotNone(self.raiz, "pasta prompts/base-de-conhecimento/pratos-mais-vendidos não encontrada")
        self.assertTrue((self.raiz / "brasil.md").is_file())
        regioes = {r for _, r in negocio.ESTADOS.values()}
        pastas = {p for p in self.raiz.iterdir() if p.is_dir() and p.name != "evidencias"}
        for pasta in pastas:
            self.assertTrue((pasta / "_regiao.md").is_file(), pasta.name)
        self.assertEqual({p.name for p in pastas}, {r.lower() for r in regioes})
        self.assertEqual({p.stem for p in (self.raiz / "evidencias").glob("*.md")}, {r.lower() for r in regioes} | {"brasil"})
        self.assertTrue((self.raiz / "README.md").is_file())

    def test_todo_arquivo_le_e_traz_o_aviso_de_dados_de_exemplo(self):
        arquivos = [p for p in self.raiz.rglob("*.md") if p.name != "README.md" and p.parent.name != "evidencias"]
        self.assertGreaterEqual(len(arquivos), 18)
        for p in arquivos:
            texto = p.read_text(encoding="utf-8")
            self.assertIn("Dados ilustrativos", texto, p.name)
            secoes = analytics.analisar(texto, p.name)  # levanta com o arquivo e a linha se algo estiver fora do formato
            self.assertTrue(secoes, p.name)
            meta, _ = analytics._meta(texto)
            self.assertEqual(meta["escopo"], secoes[0].escopo, p.name)
            if meta.get("estado"):
                self.assertIn(meta["estado"], negocio.ESTADOS, p.name)
                self.assertEqual(negocio.ESTADOS[meta["estado"]][1], meta["regiao"], p.name)
                self.assertEqual(p.parent.name, meta["regiao"].lower(), p.name)

    def test_secoes_sao_coerentes(self):
        secoes = analytics.carregar(self.raiz)
        por_escopo = {e: sum(1 for s in secoes if s.escopo == e) for e in analytics.ESCOPOS}
        self.assertEqual((por_escopo["pais"], por_escopo["regiao"]), (1, 5))
        self.assertGreaterEqual(por_escopo["estado"], 12)
        self.assertGreater(por_escopo["cidade"], por_escopo["estado"])
        self.assertGreater(por_escopo["zona"], por_escopo["cidade"])
        zonas = set(negocio.ZONAS.values())
        for s in secoes:
            nomes = [p.nome for p in s.pratos]
            self.assertEqual(len(nomes), len(set(nomes)), f"prato repetido em {s.arquivo} {s.escopo} {s.cidade} {s.zona}")
            self.assertEqual(s.pratos[0].indice, 100, s.arquivo)
            self.assertEqual([p.posicao for p in s.pratos], list(range(1, len(s.pratos) + 1)))
            self.assertEqual([p.indice for p in s.pratos], sorted((p.indice for p in s.pratos), reverse=True))
            for p in s.pratos:
                self.assertLessEqual(p.preco_min, p.preco_medio, p.nome)
                self.assertLessEqual(p.preco_medio, p.preco_max, p.nome)
                self.assertTrue(p.ingredientes.strip() and p.categoria.strip(), p.nome)
            if s.escopo == "zona":
                self.assertIn(s.zona, zonas, s.arquivo)

    def test_o_ranking_muda_com_a_zona_e_o_preco_tambem(self):
        sul = analytics.consultar({"estado": "SP", "cidade": "São Paulo", "zona": "sul"}, "prioridade", 8)
        leste = analytics.consultar({"estado": "SP", "cidade": "São Paulo", "zona": "leste"}, "prioridade", 8)
        self.assertNotEqual([m["prato"] for m in sul["ranking"]], [m["prato"] for m in leste["ranking"]])
        self.assertTrue(all(m["onde"][0]["escopo"] == "zona" for m in sul["ranking"][:3]))
        preco = lambda r, nome: next(m["preco_medio"] for m in r["ranking"] if m["prato"] == nome)  # noqa: E731
        self.assertGreater(preco(sul, "Prato feito (PF) com bife"), preco(leste, "Prato feito (PF) com bife"))  # zona sul cobra mais que a leste
        salvador = analytics.consultar({"estado": "BA", "cidade": "Salvador", "zona": "sul"}, "prioridade", 5)
        self.assertTrue(any("Moqueca" in m["prato"] for m in salvador["ranking"]))
        manaus = analytics.consultar({"estado": "AM", "cidade": "Manaus", "zona": "centro"}, "prioridade", 5)
        self.assertTrue(any(n in m["prato"] for m in manaus["ranking"] for n in ("Tambaqui", "Tacacá", "Açaí")))

    def test_e_prato_por_igualdade(self):
        self.assertTrue(analytics.e_prato("Coxinha de frango"))
        self.assertTrue(analytics.e_prato("coxinha de frango (porção com 6)"))
        self.assertTrue(analytics.e_prato("Feijoada completa"))
        self.assertTrue(analytics.e_prato("Arroz carreteiro"))
        for ingrediente in ("arroz", "frango", "feijão", "pizza", "salada", ""):
            self.assertFalse(analytics.e_prato(ingrediente), ingrediente)


class LocalizacaoTests(StoreTestCase):
    def test_estado_cidade_e_zona_no_perfil(self):
        salvo = Store().negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "sp", "cidade": "  São   Paulo ", "zona": "sul"})
        self.assertEqual((salvo["estado"], salvo["cidade"], salvo["zona"]), ("SP", "São Paulo", "sul"))
        local = negocio.localizacao(salvo)
        self.assertEqual((local["regiao"], local["estado_nome"], local["zona"], local["pais"]), ("Sudeste", "São Paulo", "Zona Sul", "Brasil"))
        self.assertEqual(negocio.descrever_local(salvo), "Zona Sul, São Paulo/SP (região Sudeste)")
        self.assertIn("local: Zona Sul, São Paulo/SP", negocio.descrever(salvo))

    def test_localizacao_e_opcional_e_perfil_antigo_continua_valendo(self):
        antigo = {"tipo": "restaurante", "vende": [], "publico": "", "observacoes": ""}  # salvo antes da localização (e do tipo de alimentação) existir
        self.assertEqual(negocio.localizacao(antigo)["estado"], "")
        self.assertIn("local da loja não informado", negocio.descrever(antigo))
        self.assertEqual(negocio.normalizar({"tipo": "restaurante"})["estado"], "")

    def test_estado_e_zona_so_da_lista(self):
        for ruim in ({"estado": "XX"}, {"zona": "nordeste"}):
            with self.assertRaises(ValueError, msg=str(ruim)):
                negocio.normalizar({"tipo": "restaurante", **ruim})

    def test_opcoes_para_a_tela(self):
        opcoes = negocio.opcoes()
        self.assertEqual(len(opcoes["estados"]), 27)
        self.assertEqual({"id": "SP", "label": "São Paulo", "regiao": "Sudeste"}, next(e for e in opcoes["estados"] if e["id"] == "SP"))
        nomes = [e["label"] for e in opcoes["estados"]]
        self.assertEqual(nomes, sorted(nomes, key=norm_text))  # ordem alfabética sem pesar o acento
        self.assertEqual(nomes[nomes.index("Pará"):nomes.index("Pará") + 3], ["Pará", "Paraíba", "Paraná"])
        self.assertEqual([z["label"] for z in opcoes["zonas"]], ["Centro", "Zona Norte", "Zona Sul", "Zona Leste", "Zona Oeste"])


class FerramentaEApiTests(StoreTestCase):
    def _loja(self):
        Store().negocio_salvar({"tipo": "restaurante_hotel", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo", "zona": "sul"})

    def test_consultora_consulta_os_mais_vendidos_da_zona_da_loja(self):
        self._loja()
        out = call("analytics_consultar")
        self.assertTrue(out["success"], out)
        self.assertEqual(out["local_da_loja"], "Zona Sul, São Paulo/SP (região Sudeste)")
        self.assertEqual(len(out["ranking"]), 8)
        self.assertIn("exemplo", out["aviso"])
        self.assertTrue(out["ranking"][0]["onde"][0].startswith("zona #"))
        frase = out["diga_a_dona_maria"]  # a consultora repete esta frase: traz o aviso de base de exemplo e os cinco primeiros
        self.assertTrue(frase.startswith("Mais vendidos (prioridade para Zona Sul, São Paulo/SP (região Sudeste); base de exemplo, dados ilustrativos): 1) "), frase)
        self.assertEqual(frase.count("(~R$ "), 5)
        self.assertEqual([e.split(":")[0] for e in out["escopos_com_dados"]], ["Zona", "Cidade", "Estado", "Região", "País"])
        cidade = call("analytics_consultar", escopo="cidade", limite=2)
        self.assertIn("(Cidade: São Paulo; base de exemplo", cidade["diga_a_dona_maria"])
        pizza = call("analytics_consultar", escopo="estado", categoria="pizza", limite=3)
        self.assertEqual({m["categoria"] for m in pizza["ranking"]}, {"pizza"})
        self.assertIn("indice_de_vendas", pizza["ranking"][0])
        self.assertFalse(call("analytics_consultar", escopo="bairro")["success"])

    def test_sem_localizacao_a_consultora_recebe_o_aviso(self):
        out = call("analytics_consultar")
        self.assertEqual(out["local_da_loja"], "não informado (Meu negócio)")
        self.assertIn("Meu negócio", out["mensagem"])
        self.assertIn("(só o país: a loja ainda não tem localização em Meu negócio; base de exemplo", out["diga_a_dona_maria"])

    def test_tela_consulta_o_analytics(self):
        status, dados = api.tratar("GET", "/api/analytics")
        self.assertEqual((status, dados["local_definido"]), (200, False))
        self._loja()
        status, dados = api.tratar("GET", "/api/analytics?escopo=zona&limite=3")
        self.assertEqual((status, len(dados["ranking"]), dados["local"]["zona"]), (200, 3, "Zona Sul"))
        self.assertEqual(api.tratar("GET", "/api/analytics?escopo=bairro")[0], 400)
        self.assertEqual(api.tratar("GET", "/api/analytics?limite=abc")[0], 400)
        self.assertEqual(api.tratar("POST", "/api/analytics")[0], 405)

    def test_perfil_com_localizacao_pela_tela(self):
        status, dados = api.tratar("PUT", "/api/negocio", {"tipo": "restaurante", "estado": "BA", "cidade": "Salvador", "zona": "centro"})
        self.assertEqual((status, dados["negocio"]["estado"]), (200, "BA"))
        self.assertEqual(api.tratar("PUT", "/api/negocio", {"tipo": "restaurante", "estado": "ZZ"})[0], 400)
        self.assertEqual(len(dados["opcoes"]["estados"]), 27)

    def test_painel_leva_evidencia_publicada_do_lugar_apenas_na_pesquisa(self):
        mensagem = "Quais os pratos mais vendidos na minha cidade?"
        sem_local = painel.texto_do_painel(mensagem=mensagem)
        self.assertIn("Retrospectiva iFood 2025", sem_local)
        self.assertNotIn("Virado à paulista", sem_local)
        self._loja()
        texto = painel.texto_do_painel(mensagem=mensagem)
        self.assertIn("Virado à paulista", texto)
        self.assertNotIn("dados de exemplo", texto)
        self.assertIn("local: Zona Sul, São Paulo/SP", texto)
        self.assertNotIn("Virado à paulista", painel.texto_do_painel(mensagem="Quanto paguei no arroz?"))


if __name__ == "__main__":
    unittest.main()
