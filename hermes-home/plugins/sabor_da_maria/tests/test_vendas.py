"""Vendas são fatos declarados, não previsões nem movimentos de estoque."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from sabor_da_maria import api, vendas
from sabor_da_maria.store import Store

from ._base import StoreTestCase

AGORA = datetime.fromisoformat("2026-12-01T12:00:00-03:00")


class VendasTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.ensure_loaded()
        self.prato = self.store.salvar_prato("Arroz da casa", None, 4, 15.18, 3.79, {})
        arroz = self.store.obter("Arroz branco tipo 1")
        self.store.aceitar_prato(self.prato, 8.50, [], reservas=[{"ingrediente_id": arroz["id"], "quantidade_base": 0.2}])

    def dados(self, **extras):
        return {"prato_id": self.prato, "quantidade": 3, "operacao": str(uuid4()), **extras}

    def registrar(self, **extras):
        return vendas.registrar(self.store, self.dados(**extras), agora=AGORA)

    def fotografia(self):
        with self.store.connect() as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                    for t in ("ingredientes", "pratos", "reservas", "compras", "carrinho", "perfil")}

    def test_pratos_e_reservas_nao_se_transformam_em_vendas(self):
        antes = self.fotografia()
        resumo = vendas.resumo(self.store, agora=AGORA)
        self.assertFalse(resumo["possui_registros"])
        for periodo in resumo["periodos"].values():
            self.assertEqual(periodo["quantidade"], 0)
            self.assertEqual(periodo["faturamento_bruto"], 0)
        self.assertEqual(vendas.consultar(self.store, agora=AGORA)["vendas"], [])
        self.assertEqual(self.fotografia(), antes)

    def test_registro_snapshot_cmv_sem_arredondamento_antecipado_e_sem_baixa(self):
        antes = self.fotografia()
        orcamento = self.store.carrinho_resumo()
        resultado = self.registrar()
        venda = resultado["venda"]
        self.assertFalse(resultado["ja_registrada"])
        self.assertEqual(venda["status"], "confirmada")
        self.assertEqual(venda["preco_unitario"], 8.5)
        self.assertEqual(Decimal(str(venda["cmv_unitario"])), Decimal("3.795"))
        self.assertEqual(venda["faturamento_bruto"], 25.5)
        self.assertEqual(venda["taxa_plataforma"], 2.55)
        self.assertEqual(venda["recebe_liquido"], 22.95)
        self.assertEqual(venda["cmv_total"], 11.39)
        self.assertEqual(venda["lucro_estimado"], 11.57)
        self.assertEqual(self.fotografia(), antes)
        self.assertEqual(self.store.carrinho_resumo(), orcamento)

    def test_custo_nome_e_preco_do_historico_nao_mudam_com_o_cardapio(self):
        dados = self.dados()
        primeira = vendas.registrar(self.store, dados, agora=AGORA)["venda"]
        with self.store.connect() as conn:
            conn.execute("UPDATE pratos SET nome='Outro nome',preco_venda=100,cmv_total=40 WHERE id=?", (self.prato,))
        repetida = vendas.registrar(self.store, dados, agora=AGORA)
        self.assertEqual(repetida["venda"], primeira)
        self.assertTrue(repetida["ja_registrada"])
        outra = self.registrar(preco_unitario="9,75")["venda"]
        self.assertEqual(outra["preco_unitario"], 9.75)
        self.assertEqual(outra["cmv_unitario"], 10)
        self.assertEqual(primeira["prato"], "Arroz da casa")
        self.assertEqual(self.store.obter_prato(self.prato)["preco_venda"], 100)

    def test_periodos_calendario_consideram_fuso_sao_paulo(self):
        self.registrar(vendido_em="2026-11-30T23:59:59-03:00", quantidade=1)
        self.registrar(vendido_em="2026-12-01T00:00:00-03:00", quantidade=2)
        self.registrar(vendido_em="2026-11-29T10:00:00-03:00", quantidade=4)
        self.registrar(vendido_em="2026-12-01T01:59:59+00:00", quantidade=8)
        resumo = vendas.resumo(self.store, agora=AGORA)
        self.assertEqual(resumo["fuso"], "America/Sao_Paulo")
        self.assertEqual(resumo["periodos"]["dia"]["quantidade"], 2)
        self.assertEqual(resumo["periodos"]["semana"]["quantidade"], 11)
        self.assertEqual(resumo["periodos"]["mes"]["quantidade"], 2)
        self.assertTrue(resumo["periodos"]["mes"]["fim"].startswith("2027-01-01"))
        self.assertTrue(resumo["periodos"]["semana"]["inicio"].startswith("2026-11-30"))

    def test_repeticao_concorrente_nao_duplica_e_payload_diferente_conflita(self):
        dados = self.dados(vendido_em="2026-12-01T09:00:00-03:00")
        with ThreadPoolExecutor(max_workers=4) as executor:
            resultados = list(executor.map(lambda _: vendas.registrar(self.store, dados, agora=AGORA), range(4)))
        self.assertEqual(len({r["venda"]["id"] for r in resultados}), 1)
        self.assertEqual(sum(not r["ja_registrada"] for r in resultados), 1)
        self.assertEqual(vendas.resumo(self.store, agora=AGORA)["periodos"]["dia"]["quantidade"], 3)
        for extras in ({"quantidade": 4}, {"preco_unitario": 9}, {"vendido_em": "2026-12-01T08:00:00-03:00"}):
            with self.subTest(extras=extras), self.assertRaises(vendas.ConflitoOperacao):
                vendas.registrar(self.store, {**dados, **extras}, agora=AGORA)

    def test_estorno_e_restauracao_reversiveis_sem_apagar_ou_mover_estoque(self):
        dados = self.dados()
        venda = vendas.registrar(self.store, dados, agora=AGORA)["venda"]
        antes = self.fotografia()
        self.assertFalse(vendas.estornar(self.store, venda["id"], agora=AGORA)["ja_estornada"])
        self.assertTrue(vendas.estornar(self.store, venda["id"], agora=AGORA)["ja_estornada"])
        self.assertEqual(vendas.resumo(self.store, agora=AGORA)["periodos"]["dia"]["quantidade"], 0)
        self.assertEqual(vendas.registrar(self.store, dados, agora=AGORA)["venda"]["status"], "estornada")
        consulta = vendas.consultar(self.store, agora=AGORA)
        self.assertEqual(consulta["total_registros"], 1)
        self.assertEqual(consulta["vendas"][0]["status"], "estornada")
        self.assertFalse(vendas.restaurar(self.store, venda["id"], agora=AGORA)["ja_restaurada"])
        self.assertEqual(vendas.resumo(self.store, agora=AGORA)["periodos"]["dia"]["quantidade"], 3)
        self.assertEqual(self.fotografia(), antes)

    def test_nao_aceita_quantidade_preco_data_ou_prato_invalidos(self):
        invalidos = ({"quantidade": 0}, {"quantidade": -1}, {"quantidade": 1.5}, {"quantidade": True},
                     {"preco_unitario": 0}, {"preco_unitario": -1}, {"preco_unitario": 8.555},
                     {"preco_unitario": "Infinity"}, {"vendido_em": "2026-12-01"},
                     {"vendido_em": "2026-12-01T10:00:00"}, {"vendido_em": "2026-12-02T00:00:00-03:00"},
                     {"prato_id": 999}, {"operacao": "sem-chave"})
        for extras in invalidos:
            with self.subTest(extras=extras), self.assertRaises(ValueError):
                self.registrar(**extras)
        rascunho, = self.store.selecionar_pratos([{"nome": "Rascunho"}])
        with self.assertRaisesRegex(ValueError, "prato aprovado"):
            self.registrar(prato_id=rascunho["id"])
        self.assertFalse(vendas.resumo(self.store, agora=AGORA)["possui_registros"])

    def test_limite_da_lista_nao_limita_os_totais_e_leitura_api_nao_grava(self):
        self.registrar(quantidade=1)
        self.registrar(quantidade=2)
        antes = self.fotografia()
        consulta = vendas.consultar(self.store, agora=AGORA, limite=1)
        self.assertEqual(len(consulta["vendas"]), 1)
        self.assertTrue(consulta["truncado"])
        self.assertEqual(consulta["total_registros"], 2)
        self.assertEqual(consulta["resumo"]["quantidade"], 3)
        with patch.object(vendas, "_agora", return_value=AGORA):
            self.assertEqual(api.tratar("GET", "/api/vendas/resumo")[0], 200)
            self.assertEqual(api.tratar("GET", "/api/vendas?periodo=mes")[1]["resumo"]["quantidade"], 3)
            self.assertEqual(api.tratar("GET", "/api/estado")[1]["vendas_resumo"]["periodos"]["dia"]["quantidade"], 3)
        self.assertEqual(self.fotografia(), antes)

    def test_api_registro_retry_conflito_e_correcao(self):
        dados = self.dados(vendido_em="2026-12-01T09:00:00-03:00")
        with patch.object(vendas, "_agora", return_value=AGORA):
            status, novo = api.tratar("POST", "/api/vendas", dados)
            self.assertEqual(status, 201, novo)
            id_ = novo["venda"]["id"]
            self.assertIn("vendas_resumo", novo["estado"])
            self.assertEqual(api.tratar("POST", "/api/vendas", dados)[0], 200)
            self.assertEqual(api.tratar("POST", "/api/vendas", {**dados, "quantidade": 2})[0], 409)
            self.assertEqual(api.tratar("POST", f"/api/vendas/{id_}/estorno")[1]["venda"]["status"], "estornada")
            self.assertEqual(api.tratar("POST", f"/api/vendas/{id_}/restaurar")[1]["venda"]["status"], "confirmada")
            self.assertEqual(api.tratar("POST", "/api/vendas/999/estorno")[0], 404)
            self.assertEqual(api.tratar("GET", "/api/vendas?periodo=ano")[0], 400)
            self.assertEqual(api.tratar("DELETE", f"/api/vendas/{id_}")[0], 405)
