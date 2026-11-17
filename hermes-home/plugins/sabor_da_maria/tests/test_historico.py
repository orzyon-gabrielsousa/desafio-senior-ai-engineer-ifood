from sabor_da_maria import api, historico
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class HistoricoTests(StoreTestCase):
    def test_mescla_alteracoes_de_abas_e_resiste_a_snapshot_pendente(self):
        conversa = {"id": "ch1", "title": "Cardápio", "sessionId": "hermes-1", "updatedAt": 5, "messages": [
            {"id": "u1", "role": "user", "content": "Primeira pergunta", "tools": [], "at": 1},
            {"id": "a1", "role": "assistant", "content": "Resposta", "tools": [], "at": 2},
        ]}
        destino = "/api/conversas/ch1"
        self.assertEqual(api.tratar("PUT", destino, {"geracao_dados": None, "conversa": conversa})[0], 200)
        antiga = {**conversa, "sessionId": None, "updatedAt": 4, "messages": [
            {**conversa["messages"][1], "content": "Resp", "pending": True},
            {"id": "u2", "role": "user", "content": "Segunda pergunta", "tools": [], "at": 3},
        ]}
        self.assertEqual(api.tratar("PUT", destino, {"geracao_dados": None, "conversa": antiga})[0], 200)
        exibida, = api.tratar("GET", "/api/conversas")[1]["conversas"]
        self.assertEqual(exibida["sessionId"], "hermes-1")
        self.assertEqual([m["content"] for m in exibida["messages"]], ["Primeira pergunta", "Resposta", "Segunda pergunta"])

    def test_reset_e_exclusao_impedem_que_aba_antiga_recrie_conversa(self):
        db = Store().db_path
        db.with_name(".geracao-dados").write_text("nova")
        conversa = {"id": "ch2", "title": "Histórico", "messages": [], "sessionId": "hermes-2", "updatedAt": 1}
        destino = "/api/conversas/ch2"
        anterior = {"geracao_dados": "antiga", "conversa": conversa}
        atual = {**anterior, "geracao_dados": "nova"}
        self.assertEqual(api.tratar("PUT", destino, anterior)[0], 409)
        self.assertEqual(api.tratar("PUT", destino, atual)[0], 200)
        self.assertEqual(api.tratar("DELETE", destino, anterior)[0], 409)
        self.assertEqual(api.tratar("DELETE", destino, atual)[0], 200)
        self.assertEqual(api.tratar("PUT", destino, atual)[0], 410)
        self.assertEqual(api.tratar("GET", "/api/conversas")[1]["conversas"], [])
        with historico.Historico(historico.diretorio(db))._abrir() as conn:
            self.assertEqual(conn.execute("SELECT session_id FROM exclusoes").fetchone(), ("hermes-2",))
