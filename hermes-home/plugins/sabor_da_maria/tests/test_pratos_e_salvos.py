"""Prato aberto no carrinho (cada ingrediente com o seu valor), pratos e ingredientes salvos avulsos e a migração do banco."""

import json
import os
import sqlite3

from sabor_da_maria import api, tools
from sabor_da_maria.store import OrcamentoEstourado, Store

from ._base import SESSAO, StoreTestCase


def call(name, **args):
    return json.loads(tools.HANDLERS[name](args, session_id=SESSAO))


COXINHA = [
    {"nome": "frango desfiado", "quantidade": 500, "unidade": "g", "preco_total": 9.0},
    {"nome": "farinha de trigo", "quantidade": 1, "unidade": "kg", "preco_total": 3.0, "ja_tem": True},
    {"nome": "caldo de galinha", "quantidade": 1, "unidade": "un", "preco_total": 2.5},
    {"nome": "margarina", "quantidade": 100, "unidade": "g", "preco_total": 3.5},
]


class PratoNoCarrinhoTests(StoreTestCase):
    def test_prato_aberto_com_o_valor_de_cada_ingrediente(self):
        store = Store()
        grupo = store.carrinho_adicionar_prato("Coxinha de frango", COXINHA)
        self.assertEqual(grupo["prato"], "Coxinha de frango")
        self.assertEqual((grupo["valor_do_prato"], grupo["a_comprar"]), (18.0, 15.0))
        self.assertEqual([(i["nome"], i["preco_total"], i["ja_tem"]) for i in grupo["ingredientes"]],
                         [("frango desfiado", 9.0, False), ("farinha de trigo", 3.0, True), ("caldo de galinha", 2.5, False), ("margarina", 3.5, False)])
        resumo = store.carrinho_resumo()
        self.assertEqual((resumo["carrinho"], resumo["saldo"]), (15.0, 65.0))  # o que ela já tem não gasta saldo

    def test_prato_sem_valor_de_ingrediente_nao_entra(self):
        store = Store()
        invalidos = [None, [], "frango", ["frango"], [{"nome": "frango"}], [{"nome": "frango", "preco_total": 0}],
                     [{"nome": "frango", "preco_total": "abc"}], [{"nome": "", "preco_total": 3}],
                     [{"nome": "frango", "preco_total": 3, "quantidade": 0}], [{"nome": f"i{n}", "preco_total": 1} for n in range(41)]]
        for ingredientes in invalidos:
            with self.assertRaises(ValueError, msg=str(ingredientes)[:60]):
                store.carrinho_adicionar_prato("Prato", ingredientes)
        with self.assertRaises(ValueError):
            store.carrinho_adicionar_prato("", COXINHA)
        self.assertEqual(store.carrinho_itens(), [])
        # o que já tem pode ficar com valor 0 (custo desconhecido); o que ela compra, não
        store.carrinho_adicionar_prato("Prato", [{"nome": "sal", "preco_total": 0, "ja_tem": True}, {"nome": "arroz", "preco_total": 5}])
        self.assertEqual(store.carrinho_resumo()["carrinho"], 5.0)

    def test_so_o_que_ela_compra_conta_no_saldo(self):
        store = Store()
        store.carrinho_adicionar("Filé", 70)  # saldo 10
        with self.assertRaises(OrcamentoEstourado) as ctx:
            store.carrinho_adicionar_prato("Coxinha", COXINHA)  # a comprar R$ 15
        self.assertEqual((ctx.exception.falta, ctx.exception.saldo), (5.0, 10.0))
        self.assertEqual(store.carrinho_pratos(), [])  # atômico: nenhum ingrediente entrou
        # o prato inteiro vale R$ 39, mas só R$ 9 são compra: cabe
        grupo = store.carrinho_adicionar_prato("Outro", [{"nome": "frango", "preco_total": 9}, {"nome": "farinha", "preco_total": 30, "ja_tem": True}])
        self.assertEqual((grupo["valor_do_prato"], grupo["a_comprar"]), (39.0, 9.0))
        self.assertEqual(store.carrinho_resumo()["saldo"], 1.0)

    def test_mesmo_prato_de_novo_soma_e_mantem_a_grafia(self):
        store = Store()
        store.carrinho_adicionar_prato("Coxinha de frango", COXINHA[:1])
        grupo = store.carrinho_adicionar_prato("coxinha de FRANGO", COXINHA[:1])
        self.assertEqual(grupo["prato"], "Coxinha de frango")
        self.assertEqual([(i["quantidade"], i["preco_total"]) for i in grupo["ingredientes"]], [(1000.0, 18.0)])
        self.assertEqual(len(store.carrinho_pratos()), 1)

    def test_ingrediente_avulso_e_do_prato_com_o_mesmo_nome_nao_se_misturam(self):
        store = Store()
        store.carrinho_adicionar("frango desfiado", 8, 500, "g")
        grupo = store.carrinho_adicionar_prato("Coxinha de frango", COXINHA[:1])
        self.assertEqual(len(store.carrinho_itens()), 2)
        self.assertEqual(len(grupo["ingredientes"]), 1)

    def test_remover_o_prato_inteiro_pelo_nome(self):
        store = Store()
        store.carrinho_adicionar_prato("Coxinha de frango", COXINHA)
        store.carrinho_adicionar_prato("Frango assado", [{"nome": "peito de frango", "preco_total": 20}])
        store.carrinho_adicionar("Couve", 5)
        self.assertEqual(len(store.carrinho_remover(nome="coxinha de frango")), 4)
        self.assertEqual([g["prato"] for g in store.carrinho_pratos()], ["Frango assado"])
        store.carrinho_adicionar_prato("Frango grelhado", [{"nome": "peito", "preco_total": 10}])
        with self.assertRaises(ValueError) as ctx:
            store.carrinho_remover(nome="frango")  # casa com os dois pratos e com um ingrediente
        self.assertIn("Frango assado", str(ctx.exception))
        self.assertEqual(store.carrinho_remover_prato("frango grelhado")[0]["prato"], "Frango grelhado")
        with self.assertRaises(KeyError):
            store.carrinho_remover_prato("Não existe")
        self.assertEqual(store.carrinho_resumo()["carrinho"], 25.0)  # Frango assado (20) + Couve (5)

    def test_linha_que_ja_tem_nao_gasta_saldo_nem_ao_mudar_a_quantidade(self):
        store = Store()
        grupo = store.carrinho_adicionar_prato("Prato", [{"nome": "arroz", "quantidade": 1, "unidade": "kg", "preco_total": 5, "ja_tem": True}])
        item = store.carrinho_atualizar_quantidade(grupo["ingredientes"][0]["id"], 1000)  # o preço proporcional passaria dos R$ 80
        self.assertEqual(item["preco_total"], 5000.0)
        self.assertEqual(store.carrinho_resumo()["carrinho"], 0.0)


class SalvosTests(StoreTestCase):
    def test_salvar_prato_e_ingrediente_avulsos(self):
        store = Store()
        prato = store.salvo_salvar("prato", "Coxinha de frango", ingredientes=COXINHA)
        self.assertEqual((prato["tipo"], prato["preco_total"], len(prato["ingredientes"])), ("prato", 18.0, 4))
        ingrediente = store.salvo_salvar("ingrediente", "Couve", preco_total="12,00", quantidade=1, unidade="maço")
        self.assertEqual((ingrediente["preco_total"], ingrediente["unidade"]), (12.0, "maço"))
        salvos = store.salvos()
        self.assertEqual([p["nome"] for p in salvos["pratos"]], ["Coxinha de frango"])
        self.assertEqual([i["nome"] for i in salvos["ingredientes"]], ["Couve"])

    def test_mesmo_nome_atualiza_em_vez_de_duplicar(self):
        store = Store()
        a = store.salvo_salvar("ingrediente", "Couve", preco_total=12)
        b = store.salvo_salvar("ingrediente", "couve", preco_total=10, quantidade=2, unidade="maço")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual((b["preco_total"], b["quantidade"]), (10.0, 2.0))
        store.salvo_salvar("prato", "Couve", ingredientes=[{"nome": "couve", "preco_total": 3}])  # prato e ingrediente convivem
        self.assertEqual((len(store.salvos()["pratos"]), len(store.salvos()["ingredientes"])), (1, 1))

    def test_entradas_invalidas(self):
        store = Store()
        for kwargs in ({"tipo": "bebida", "nome": "x"}, {"tipo": "ingrediente", "nome": ""}, {"tipo": "ingrediente", "nome": "sal"},
                       {"tipo": "ingrediente", "nome": "sal", "preco_total": 0}, {"tipo": "ingrediente", "nome": "sal", "preco_total": 3, "quantidade": -1},
                       {"tipo": "prato", "nome": "Sem ingredientes"}, {"tipo": "prato", "nome": "Sem valor", "ingredientes": [{"nome": "a"}]}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                store.salvo_salvar(**kwargs)
        self.assertEqual(store.salvos(), {"pratos": [], "ingredientes": []})

    def test_por_um_salvo_no_carrinho(self):
        store = Store()
        prato = store.salvo_salvar("prato", "Coxinha de frango", ingredientes=COXINHA)
        out = store.carrinho_do_salvo(prato["id"])
        self.assertEqual((out["tipo"], out["prato"]["valor_do_prato"]), ("prato", 18.0))
        ingrediente = store.salvo_salvar("ingrediente", "Couve", preco_total=12, quantidade=1, unidade="maço")
        self.assertEqual(store.carrinho_do_salvo(ingrediente["id"])["item"]["nome"], "Couve")
        self.assertEqual(store.carrinho_resumo()["saldo"], 53.0)  # 80 − 15 (o que compra da coxinha) − 12
        lagosta = store.salvo_salvar("ingrediente", "Lagosta", preco_total=95)
        with self.assertRaises(OrcamentoEstourado):
            store.carrinho_do_salvo(lagosta["id"])
        with self.assertRaises(KeyError):
            store.carrinho_do_salvo(999)

    def test_salvar_o_que_ja_esta_no_carrinho(self):
        store = Store()
        store.carrinho_adicionar_prato("Coxinha de frango", COXINHA)
        store.carrinho_adicionar("Couve", 12, 1, "maço")
        prato = store.salvar_do_carrinho(prato="coxinha de frango")
        self.assertEqual((len(prato["ingredientes"]), prato["preco_total"]), (4, 18.0))
        self.assertTrue(any(i["ja_tem"] for i in prato["ingredientes"]))
        self.assertEqual(store.salvar_do_carrinho(nome="couve")["preco_total"], 12.0)
        with self.assertRaises(KeyError):
            store.salvar_do_carrinho(prato="Nada")
        with self.assertRaises(KeyError):
            store.salvar_do_carrinho(nome="frango desfiado")  # está dentro de um prato, não avulso

    def test_apagar_salvo(self):
        store = Store()
        item = store.salvo_salvar("ingrediente", "Couve", preco_total=12)
        self.assertEqual(store.salvo_remover(item["id"])["nome"], "Couve")
        with self.assertRaises(KeyError):
            store.salvo_remover(item["id"])

    def test_banco_criado_antes_das_colunas_novas_e_migrado(self):
        con = sqlite3.connect(os.environ["SABOR_DA_MARIA_DB"])
        con.executescript(
            "CREATE TABLE carrinho (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, quantidade REAL NOT NULL DEFAULT 1, unidade TEXT,"
            " preco_total REAL NOT NULL, estimado INTEGER NOT NULL DEFAULT 0, origem TEXT NOT NULL DEFAULT 'manual', receita_id INTEGER,"
            " motivo TEXT, criado_em TEXT NOT NULL);"
            " INSERT INTO carrinho(nome, quantidade, preco_total, criado_em) VALUES ('Couve', 1, 12, '2026-11-14');")
        con.commit()
        con.close()
        store = Store()
        item = store.carrinho_itens()[0]
        self.assertEqual((item["nome"], item["prato"], item["ja_tem"]), ("Couve", None, False))
        store.carrinho_adicionar_prato("Prato", COXINHA[:1])  # as colunas novas funcionam no banco antigo
        self.assertEqual(store.carrinho_resumo()["carrinho"], 21.0)
        Store()  # abrir de novo não tenta migrar outra vez


class FerramentasDePratoTests(StoreTestCase):
    def test_consultora_adiciona_o_prato_aberto(self):
        out = call("carrinho_adicionar_prato", prato="Coxinha de frango", ingredientes=COXINHA)
        self.assertTrue(out["success"], out)
        self.assertEqual(out["resumo"]["saldo"], 65.0)
        texto = out["diga_a_dona_maria"]
        for trecho in ("Coxinha de frango: R$ 18,00 (a comprar R$ 15,00)", "- frango desfiado (500 g): R$ 9,00",
                       "- farinha de trigo (1 kg): R$ 3,00 — já tem na despensa", "saldo R$ 65,00"):
            self.assertIn(trecho, texto)
        self.assertEqual(out["carrinho"], [])  # o prato não aparece como ingrediente avulso
        self.assertEqual(out["pratos"][0]["prato"], "Coxinha de frango")

    def test_prato_sem_ingredientes_ou_sem_valor_volta_pedindo(self):
        out = call("carrinho_adicionar_prato", prato="Coxinha de frango")
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertIn("informe os ingredientes", out["error"])
        out = call("carrinho_adicionar_prato", prato="Coxinha", ingredientes=[{"nome": "frango"}])
        self.assertFalse(out["success"])
        self.assertIn("informe o valor de frango", out["error"])
        self.assertEqual(call("carrinho_consultar")["pratos"], [])

    def test_prato_nao_entra_como_item_unico_de_preco_fechado(self):
        # O caso da demo: "coxinha de frango" entrou como um item só, R$ 18 estimado. Agora a tool recusa e manda abrir o prato.
        out = call("carrinho_adicionar", nome="Coxinha de frango", preco_total=18, preco_estimado=True)
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertIn("é um prato", out["error"])
        self.assertIn("carrinho_adicionar_prato", out["proximos_passos"][0])
        self.assertFalse(call("carrinho_adicionar", nome="Feijoada completa", preco_total=52)["success"])
        call("salvos_salvar", tipo="prato", nome="Meu prato da casa", ingredientes=[{"nome": "arroz", "preco_total": 5}])
        self.assertFalse(call("carrinho_adicionar", nome="meu prato da casa", preco_total=10)["success"])
        self.assertEqual(call("carrinho_consultar")["carrinho"], [])
        # ingrediente continua entrando, mesmo com nome que começa como um prato ("Arroz carreteiro" é prato; "arroz" não)
        self.assertTrue(call("carrinho_adicionar", nome="arroz", preco_total=5)["success"])

    def test_prato_salvo_volta_ao_carrinho_pelo_nome(self):
        call("salvos_salvar", tipo="prato", nome="Coxinha de frango", ingredientes=COXINHA)
        out = call("carrinho_adicionar_prato", prato="coxinha de frango")
        self.assertTrue(out["success"], out)
        self.assertEqual(out["pratos"][0]["valor_do_prato"], 18.0)

    def test_adiciona_e_salva_de_uma_vez(self):
        out = call("carrinho_adicionar_prato", prato="Coxinha de frango", ingredientes=COXINHA, salvar=True)
        self.assertEqual(out["salvo_em_meus_pratos"], "Coxinha de frango")
        self.assertEqual(call("carrinho_consultar")["salvos"]["pratos"][0]["nome"], "Coxinha de frango")

    def test_prato_que_nao_cabe_no_saldo(self):
        out = call("carrinho_adicionar_prato", prato="Lagosta", ingredientes=[{"nome": "lagosta", "preco_total": 95}])
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual(out["falta"], 15.0)

    def test_remover_o_prato_pelo_nome(self):
        call("carrinho_adicionar_prato", prato="Coxinha de frango", ingredientes=COXINHA)
        out = call("carrinho_remover", nome="coxinha de frango")
        self.assertTrue(out["success"])
        self.assertEqual(len(out["removidos"]), 4)
        self.assertEqual((out["resumo"]["saldo"], out["pratos"]), (80.0, []))

    def test_salvar_pela_consultora_o_que_esta_no_carrinho_ou_avulso(self):
        call("carrinho_adicionar_prato", prato="Coxinha de frango", ingredientes=COXINHA)
        out = call("salvos_salvar", tipo="prato", nome="Coxinha de frango")  # sem lista: guarda o do carrinho
        self.assertTrue(out["success"], out)
        self.assertIn("Salvei o prato Coxinha de frango (4 ingredientes, R$ 18,00)", out["diga_a_dona_maria"])
        out = call("salvos_salvar", tipo="ingrediente", nome="couve", preco_total=12, quantidade=1, unidade="maço")
        self.assertTrue(out["success"])
        self.assertEqual(out["salvos"]["ingredientes"][0]["nome"], "couve")
        for ruim in ({"tipo": "bebida", "nome": "x"}, {"tipo": "prato", "nome": "Nada no carrinho"}, {"tipo": "ingrediente", "nome": "sem preço"}):
            self.assertFalse(call("salvos_salvar", **ruim)["success"], ruim)


class ApiDePratosTests(StoreTestCase):
    def test_prato_pela_tela(self):
        status, dados = api.tratar("POST", "/api/carrinho/pratos", {"prato": "Coxinha de frango", "ingredientes": COXINHA})
        self.assertEqual((status, dados["orcamento"]["saldo"]), (201, 65.0))
        linhas = [i for i in dados["carrinho"] if i["prato"] == "Coxinha de frango"]
        self.assertEqual((len(linhas), sum(i["ja_tem"] for i in linhas)), (4, 1))
        self.assertEqual(call("carrinho_consultar")["pratos"][0]["a_comprar"], 15.0)  # mesma base que a consultora lê
        status, dados = api.tratar("DELETE", "/api/carrinho/pratos/Coxinha%20de%20frango")
        self.assertEqual((status, dados["carrinho"], dados["orcamento"]["saldo"]), (200, [], 80.0))
        self.assertEqual(api.tratar("DELETE", "/api/carrinho/pratos/Nada")[0], 404)
        status, dados = api.tratar("POST", "/api/carrinho/pratos", {"prato": "X", "ingredientes": [{"nome": "a"}]})
        self.assertEqual(status, 400)
        self.assertIn("valor", dados["erro"])
        status, dados = api.tratar("POST", "/api/carrinho/pratos", {"prato": "Lagosta", "ingredientes": [{"nome": "lagosta", "preco_total": 95}]})
        self.assertEqual((status, dados["falta"]), (409, 15.0))

    def test_salvos_pela_tela(self):
        status, dados = api.tratar("POST", "/api/salvos", {"tipo": "prato", "nome": "Coxinha de frango", "ingredientes": COXINHA})
        self.assertEqual((status, dados["salvos"]["pratos"][0]["preco_total"]), (201, 18.0))
        status, dados = api.tratar("POST", "/api/salvos", {"tipo": "ingrediente", "nome": "Couve", "preco_total": "12,00", "quantidade": 1, "unidade": "maço"})
        self.assertEqual(status, 201)
        id_prato, id_couve = dados["salvos"]["pratos"][0]["id"], dados["salvos"]["ingredientes"][0]["id"]
        self.assertEqual(call("carrinho_consultar")["salvos"]["pratos"][0]["nome"], "Coxinha de frango")  # tela → consultora
        status, dados = api.tratar("POST", f"/api/salvos/{id_prato}/carrinho")
        self.assertEqual((status, dados["orcamento"]["saldo"]), (201, 65.0))
        status, dados = api.tratar("POST", f"/api/salvos/{id_couve}/carrinho")
        self.assertEqual((status, dados["orcamento"]["saldo"]), (201, 53.0))
        status, dados = api.tratar("DELETE", f"/api/salvos/{id_prato}")
        self.assertEqual((status, dados["salvos"]["pratos"]), (200, []))
        for metodo, caminho, corpo, esperado in (("DELETE", "/api/salvos/999", None, 404), ("POST", "/api/salvos/999/carrinho", None, 404),
                                                 ("POST", "/api/salvos/abc/carrinho", None, 400), ("POST", "/api/salvos", {"tipo": "bebida", "nome": "x"}, 400),
                                                 ("GET", "/api/salvos", None, 405)):
            self.assertEqual(api.tratar(metodo, caminho, corpo)[0], esperado, (metodo, caminho))
