#!/usr/bin/env python3
"""Mede a conversa real em um Hermes temporário, sem copiar os bancos da pessoa.

Requer a stack ligada. Saída: conversa, fonte e resumo JSON na pasta informada.
O container, a chave sintética e os bancos usados no teste são descartados.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from fluxo_assertions import assert_contract, fold

REPO = Path(__file__).resolve().parents[1]
REQUEST = ("Monte um prato para meu cardápio com frango e batata da minha despensa. "
           "Pesquise na web a receita Frango assado com batatas do TudoGostoso. Use a busca para encontrar "
           "a página direta desse título. Leia a receita completa e confira ingredientes, duração e preparo "
           "com minha cozinha cadastrada. Pode propor uma adaptação para quatro porções usando a despensa, "
           "explicando o que mudou em relação à fonte. Apresente uma única versão e peça meu feedback antes de aprovar.")
CONTINUE_REQUEST = ("[painel da tela: cadastro guiado no chat; salvou negocio]\n"
                    "Enviei meu cadastro do negócio pelo chat. Pode conduzir os próximos passos, uma decisão de cada vez.")
# O que o botão "Não gostei, iniciar nova busca" do chat envia (playground/src/lib/prompts.ts).
NOVA_BUSCA = ("Quero sua ajuda. Pesquise outras receitas reais para o meu negócio com o estoque disponível, "
              "diferentes das que você já mostrou. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.")
# Nomes de ferramenta e de campo, instruções escritas para o modelo e entidades HTML cruas da página:
# nada disso é fala para a pessoa. Vale sobre o texto exato da resposta ("adaptacao" sem acento é o nome do campo).
TERMOS_INTERNOS = re.compile(r"preco_compra_estimado|quantidade_compra|unidade_compra|conteudo_por_unidade|despensa_\w+|"
                             r"receita_conferir\w*|web_search|web_extract|medida não estruturada|medida de uso proposta|"
                             r"\badaptacao\b|&#?\w+;|"
                             # recusa de ferramenta repetida para ela: ordem ao modelo, e ela citada em terceira pessoa
                             r"(?:^|\n)\s*Pergunte\b|\b(?:da|à|pela) Dona Maria\b")
DOIS_PRATOS = ("quero esses dois 2. Frango com Quiabo (Panelinha) "
               "1. Marmita de Frango com Quiabo Frito")
FONTE_BATATA = {
    "nome": "Frango com Batata Fácil",
    "url": "https://receitasdeminuto.com/marmita-da-semana-frango-com-batata-facil/",
    "ingredientes": ["12 coxinhas da asa de Frango", "4 batatas médias", "1/2 cebola (picada)",
                     "2 dentes de alho (triturado)", "1/2 Xícara (chá de água)", "Pimenta Calabresa",
                     "Paprica Doce", "Sal", "Salsinha", "Azeite a gosto"],
    "porcoes": 4.0, "tempo_preparo_min": None,
    "modo_preparo": "1. Em uma panela adicione o azeite e refogue a cebola até ficar transparente, junte o alho e o frango e frite um pouco (cerca de 2 minutos).\n"
                    "2. Tempere com pimenta, sal, paprica (ou os temperos que preferir) misture bem.\n"
                    "3. Adicione as batatas descascadas e picadas em cubos médios, junte a água e deixe cozinhar por cerca de 30 minutos, misturando de vez em quando para não grudar.\n"
                    "4. Finalize com as ervas frescas ou caso esteja usando desidratada coloque junto com os outros temperos no inicio do preparo.",
}
RETOMAR_BATATA = ("Sobre o prato escolhido #1 (Frango com Batata Fácil): Vamos continuar «Frango com Batata Fácil». "
                  "Confira o que já está salvo e me conduza pelo próximo passo, perguntando só o que falta.")
HISTORICO_DOIS = [
    {"role": "assistant", "content": "### 1. Marmita de Frango com Quiabo Frito\n"
     "Fonte: [Marmita de Frango com Quiabo Frito](https://www.clickreceita.com.br/receita/marmita-frango-com-quiabo-frito.html)\n"
     "Já tem: frango, cebola e alho. Precisa conferir ou comprar: quiabo.\n"
     "### 2. Frango com Quiabo (Panelinha)\n"
     "Fonte: [Frango com quiabo](https://panelinha.com.br/receita/frango-com-quiabo)\n"
     "Precisa conferir: ingredientes e preparo na fonte antes de montar a ficha.\n"
     "Você gosta de cozinhar uma dessas opções? Vê algum impedimento?"},
    {"role": "user", "content": "Quero montar meu próprio prato."},
    {"role": "assistant", "content": "Qual prato ou ingrediente você tem em mente?"},
]


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def state(home: Path) -> dict:
    database = home / "sabor_da_maria/sabor_da_maria.db"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        result = {table: [dict(row) for row in conn.execute("SELECT * FROM " + table)]
                  for table in ("receitas", "pratos", "analises_preco", "reservas", "carrinho", "compras", "vendas")}
        result["perfil"] = {row["chave"]: json.loads(row["valor"]) for row in conn.execute("SELECT chave,valor FROM perfil")}
    for table in ("receitas", "pratos", "analises_preco"):
        for row in result[table]:
            row["dados"] = json.loads(row["dados"])
    return result


def source_snapshots(home: Path) -> list[dict]:
    database = home / "state.db"
    if not database.exists():
        return []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT content, api_content FROM messages WHERE role='tool' ORDER BY id").fetchall()
    pages = []

    def walk(value):
        if isinstance(value, str):
            decoder = json.JSONDecoder()
            for match in re.finditer(r"(?m)^\s*[\[{]", value):
                try:
                    parsed, _ = decoder.raw_decode(value, match.end() - 1)
                except ValueError:
                    continue
                walk(parsed)
                break
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if value.get("url") and "## Ingredientes" in str(value.get("content", "")):
                pages.append(value)
            for item in value.values():
                walk(item)

    for row in rows:
        for value in row:
            walk(value)
    return pages


def visible_recipe(data: dict, text: str) -> dict | None:
    dishes = {d["id"]: d for d in data["pratos"]}
    recipes = {r["id"]: r for r in data["receitas"]}
    for dish_id in reversed(re.findall(r"prato\s*#\s*(\d+)", text, re.I)):
        dish = dishes.get(int(dish_id))
        recipe = recipes.get(dish.get("receita_id")) if dish else None
        if recipe and recipe["dados"].get("veredito") == "viavel":
            return recipe
    return next((r for r in reversed(data["receitas"]) if r["dados"].get("veredito") == "viavel"
                 and r["nome"].lower() in text.lower()), None)


def tool_metrics(events: list[dict], duration: float) -> dict:
    calls = {}
    for event in events:
        identifier = event.get("toolCallId")
        if not identifier:
            continue
        item = calls.setdefault(identifier, {"nome": event.get("tool"), "inicio": event["segundos"]})
        if event.get("status") == "completed":
            item["fim"] = event["segundos"]
    intervals = sorted((x["inicio"], x["fim"]) for x in calls.values() if "fim" in x)
    total = 0.0
    end = 0.0
    for start, stop in intervals:
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return {"chamadas": len(calls), "por_ferramenta": dict(Counter(x["nome"] for x in calls.values())),
            "tempo_em_ferramentas_s": round(total, 3),
            "tempo_fora_ferramentas_s": round(max(0, duration - total), 3)}


class Conversation:
    def __init__(self, base: str, key: str, home: Path, output: Path, timeout: int):
        self.base, self.key, self.home, self.output, self.timeout = base, key, home, output, timeout
        self.session = None
        self.turns = []

    def turn(self, message: str, *, history: list[dict] | None = None) -> str:
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}
        if self.session:
            headers["X-Hermes-Session-Id"] = self.session
        body = json.dumps({"model": "hermes-agent", "stream": True,
                           "messages": [*(history or []), {"role": "user", "content": message}]}).encode()
        request = urllib.request.Request(self.base + "/v1/chat/completions", data=body, headers=headers)
        chunks, events = [], []
        started = time.monotonic()
        first_text = None
        print("Enviando: " + message, flush=True)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            self.session = response.headers.get("X-Hermes-Session-Id") or self.session
            event = "message"
            for raw in response:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if event == "hermes.tool.progress" or ("choices" not in payload and "tool" in payload):
                    events.append({**payload, "segundos": round(time.monotonic() - started, 3)})
                for choice in payload.get("choices", []):
                    text = choice.get("delta", {}).get("content")
                    if text:
                        first_text = first_text if first_text is not None else time.monotonic() - started
                        chunks.append(text)
                event = "message"
        duration = time.monotonic() - started
        text = "".join(chunks)
        replacements = list(re.finditer(r"(?:^|\n\n)\[(?:Response transformed after streaming|Resposta transformada após o streaming)\]\n", text))
        if replacements:
            text = text[replacements[-1].end():]
        self.turns.append({"pergunta": message, "resposta": text, "segundos": round(duration, 3),
                           "primeiro_texto_s": round(first_text, 3) if first_text is not None else None,
                           "ferramentas": events, **tool_metrics(events, duration)})
        write_json(self.output / "conversa.json", self.turns)
        write_json(self.output / "fontes.json", source_snapshots(self.home))
        print(f"Resposta em {duration:.2f}s; {self.turns[-1]['chamadas']} chamadas de ferramenta.", flush=True)
        print(text[:1000], flush=True)
        return text


def fixture(home: Path, case: str):
    os.environ.update(HERMES_HOME=str(home), SABOR_DA_MARIA_DB=str(home / "sabor_da_maria/sabor_da_maria.db"),
                      SABOR_DA_MARIA_XLSX=str(REPO / "data/despensa_dona_maria.xlsx"), SABOR_DA_MARIA_ORCAMENTO="80")
    sys.path.insert(0, str(REPO / "hermes-home/plugins"))
    from sabor_da_maria import cozinha
    from sabor_da_maria.store import Store
    store = Store()
    store.ensure_loaded()
    equipment = {i.id: (6 if i.id == "fogao" else True) for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE}
    skills = {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE}
    if case == "conducao":
        # Cadastro progressivo: utensílios irrelevantes para estes preparos seguem desconhecidos.
        equipment = {key: equipment[key] for key in ("fogao", "forno", "forma", "panela_grande", "faca", "geladeira")}
        skills = {key: skills[key] for key in ("assar", "refogar", "arroz_feijao")}
    if case == "cozinha-incompleta":
        equipment.pop("forno", None)
    if case not in ("escolha-dois", "retomada-fonte"):
        store.cozinha_salvar({"equipamentos": equipment, "habilidades": skills,
                              "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120,
                                         "gas": "botijão", "energia": "estável"}})
    store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira", "marmita"],
                          "estado": "SP", "cidade": "São Paulo", "zona": "leste"})
    if case in ("completo", "escolha-dois", "retomada-fonte", "nova-busca", "adaptacao-flexivel"):
        from sabor_da_maria import jornada

        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "prato_proprio" if case == "escolha-dois" else "ajuda_ia")
    if case == "retomada-fonte":
        from sabor_da_maria import fonte

        dish, = store.selecionar_pratos([{"nome": FONTE_BATATA["nome"], "fonte_url": FONTE_BATATA["url"]}])
        ingredients = [{"nome": "coxinhas da asa de frango", "quantidade": 12, "unidade": "un"},
                       {"nome": "batatas medias", "quantidade": 4, "unidade": "un"},
                       {"nome": "cebola picada", "quantidade": 0.5, "unidade": "kg"},
                       {"nome": "alho triturado", "quantidade": 2, "unidade": "un"},
                       {"nome": "agua", "quantidade": 0.5, "unidade": "L"},
                       *[{"nome": name, "quantidade": 1, "unidade": "kg"} for name in ("Pimenta Calabresa", "Paprica Doce", "Sal", "Salsinha")]]
        store.salvar_receita(FONTE_BATATA["nome"], FONTE_BATATA["url"], 4, {
            "ingredientes": ingredients, "tempo_preparo_min": 32, "fonte_verificada": FONTE_BATATA,
            "fidelidade_fonte": fonte.conferir(FONTE_BATATA, ingredients, 4),
            "origem_montagem": "fonte_web", "adaptacao": None,
            "equipamentos_necessarios": ["fogão"], "tecnicas_necessarias": ["refogar"],
            "modo_preparo": FONTE_BATATA["modo_preparo"]}, "conferida", prato_id=dish["id"])
    if case == "adaptacao-flexivel":
        from sabor_da_maria import tools

        # Ficha sintética de regressão; a URL só representa a referência já salva.
        ingredients = [{"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"},
                       {"nome": "Fermento biológico", "quantidade": 5, "unidade": "g"},
                       {"nome": "Água", "quantidade": 250, "unidade": "ml"},
                       {"nome": "Peito de frango", "quantidade": 300, "unidade": "g"},
                       {"nome": "Molho de tomate", "quantidade": 150, "unidade": "g"},
                       {"nome": "Queijo mussarela", "quantidade": 150, "unidade": "g"},
                       {"nome": "Orégano a gosto"}]
        source = {"nome": "Pizza de frango", "url": "https://www.tudogostoso.com.br/receita/197822-pizza-de-frango.html",
                  "porcoes": 4, "tempo_preparo_min": 90,
                  "ingredientes": ["500 g de farinha de trigo", "5 g de fermento biológico", "250 ml de água",
                                   "300 g de peito de frango", "150 g de molho de tomate", "150 g de queijo mussarela", "Orégano a gosto"],
                  "modo_preparo": "Misture e fermente a massa. Cozinhe e desfie o frango, cubra com molho, frango, queijo e orégano e asse no forno."}
        dish, = store.selecionar_pratos([{"nome": source["nome"], "fonte_url": source["url"]}])
        store.salvar_receita(source["nome"], source["url"], 4, {
            "ingredientes": ingredients, "fonte_verificada": source, "ficha_original_da_fonte": True,
            "modo_preparo": source["modo_preparo"], "tempo_preparo_min": 90,
            "resultado": tools._resolver_ingredientes(store, ingredients)}, "conferida", prato_id=dish["id"])
    if case not in ("completo", "conducao", "escolha-dois", "retomada-fonte", "nova-busca", "adaptacao-flexivel"):
        ingredient = {"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}
        name = "Frango ao forno"
        if case == "embalagem-sem-peso":
            name = "Cobertura de chocolate da casa"
            ingredient = {"nome": "Cobertura de chocolate", "quantidade": 100, "unidade": "g"}
        if case == "orcamento-insuficiente":
            ingredient["quantidade"], ingredient["unidade"] = 18, "kg"
        dish, = store.selecionar_pratos([{"nome": name}])
        store.salvar_receita(name, None, 4, {"ingredientes": [ingredient], "tempo_preparo_min": 50,
                             "equipamentos_necessarios": ["forno", "forma"], "tecnicas_necessarias": ["assar"]},
                             "conferida", prato_id=dish["id"])
        if case == "vendas":
            saved = store.obter_prato(dish["id"])
            store.salvar_prato(name, saved["receita_id"], 4, 7, 1.75, {}, prato_id=dish["id"])
            chicken = store.obter("Peito de frango")
            store.aceitar_prato(dish["id"], 6.50, [], reservas=[{"ingrediente_id": chicken["id"], "quantidade_base": 0.5}])
    return store


def check_flexible_adaptation(conversation: Conversation, home: Path, store) -> dict:
    """LLM monta a versão local e o aceite posterior libera a etapa de preço."""
    from sabor_da_maria import jornada, tools

    before, stock = state(home), store.ingredientes()
    text = conversation.turn("Quero salvar o prato #1 sem fermento e sem orégano, usando o que tenho. "
                             "Faça uma massa fina sem fermentação e use o tomate para preparar o molho. "
                             "Pode propor as quantidades locais para quatro porções; salve e mostre essa versão para meu aceite.")
    data = state(home)
    dish, = data["pratos"]
    recipe = next(r for r in data["receitas"] if r["id"] == dish["receita_id"])
    assert dish["id"] == before["pratos"][0]["id"] and dish["status"] == "selecionado", "a adaptação trocou a ficha ou virou aceite"
    assert recipe["dados"]["fonte_verificada"] == before["receitas"][0]["dados"]["fonte_verificada"], "a referência original mudou"
    assert recipe["dados"].get("adaptacao") and recipe["dados"].get("preparo_da_adaptacao"), "a LLM não salvou preparo e adaptação"
    assert not any(re.search(r"fermento|oregano", fold(i["nome"])) for i in recipe["dados"]["ingredientes"]), "omitidos voltaram ao lote"
    assert {fold(n["ingrediente"]) for n in recipe["dados"]["ingredientes_faltantes"] if n["estado"] == "omitido_na_adaptacao"} >= {"fermento biologico", "oregano a gosto"}, "faltas não foram preservadas"
    assert tools._avaliar_prato(store, recipe["dados"]["ingredientes"], recipe)["veredito"] == "viavel", "a adaptação ainda não está conferida"
    assert store.ingredientes() == stock and store.orcamento()["saldo"] == 80, "salvar simulou estoque ou gasto"
    assert not data["reservas"] and not data["compras"] and not data["carrinho"] and not data["analises_preco"], "rascunho criou compromisso"
    assert any(e.get("tool") == "receita_conferir_ingredientes" for e in conversation.turns[-1]["ferramentas"]), "a LLM só prometeu salvar"
    assert "ferment" in fold(text) and not TERMOS_INTERNOS.search(text), "a resposta escondeu a adaptação ou expôs campos"
    conversation.turn("Sim, aceito preparar o prato #1 nessa versão de massa fina sem fermento.")
    assert store.analises_preco(), "o aceite da adaptação não gerou cenários"
    assert jornada.resumo(store)["etapa"] == "preco", "a jornada não saiu da montagem"
    assert store.orcamento()["saldo"] == 80, "o aceite de uma versão sem compras gastou complementos"
    return {"adaptacao_salva_pela_llm": True, "etapa_apos_aceite": "preco", "saldo_complementos": 80,
            "referencia_preservada": True, "ingredientes_omitidos_anotados": True}


def check_two_choices(conversation: Conversation, home: Path, store) -> dict:
    """Nomes/links são contexto de escolha; quantidades vêm da leitura real."""
    before = state(home)
    before_stock = store.ingredientes()
    text = conversation.turn(DOIS_PRATOS, history=HISTORICO_DOIS)
    data = state(home)
    expected = {"Marmita de Frango com Quiabo Frito", "Frango com Quiabo (Panelinha)"}
    assert {p["nome"] for p in data["pratos"]} == expected, "a escolha explícita dos dois pratos não foi persistida separadamente"
    assert all(p["status"] == "selecionado" and p["preco_venda"] is None for p in data["pratos"]), "seleção virou aceite ou preço"
    assert any(p.get("receita_id") for p in data["pratos"]), "o agente só confirmou nomes sem montar a primeira ficha"
    first = next(p for p in data["pratos"] if p["nome"] == "Marmita de Frango com Quiabo Frito")
    recipe = next(r for r in data["receitas"] if r["id"] == first.get("receita_id"))
    contract = recipe["dados"].get("fonte_verificada")
    assert contract and "marmita-frango-com-quiabo-frito" in contract["url"], "a primeira ficha não preservou a fonte escolhida"
    assert not recipe["dados"].get("fidelidade_fonte", {}).get("bloqueios"), "a primeira ficha perdeu ingredientes ou medidas que já estavam na fonte"
    assert not recipe["dados"].get("fidelidade_fonte", {}).get("faltantes"), "a seleção virou adaptação omitindo ingredientes da fonte sem necessidade"
    assert recipe["dados"]["bloqueios"], "cozinha vazia foi tratada como confirmada"
    assert not recipe["dados"].get("confirmacoes_operacionais"), "inventou confirmação de gás/energia/refrigeração"
    assert store.ingredientes() == before_stock and store.carrinho_resumo()["saldo"] == 80
    for table in ("reservas", "compras", "carrinho", "vendas", "analises_preco", "perfil"):
        assert data[table] == before[table], f"seleção alterou {table} sem autorização"
    assert "combinar" not in fold(text) and "escolher um unico" not in fold(text), "voltou a perguntar se a pessoa queria os dois"
    assert "#None" not in text and "ValueError" not in text, "a resposta expôs referência ou erro interno"
    assert text.count("?") == 1, "a montagem apresentou várias perguntas ao mesmo tempo"
    calls = {e.get("tool") for t in conversation.turns for e in t["ferramentas"]}
    assert {"web_extract", "receita_conferir_ingredientes"} <= calls, "a montagem delegada não executou leitura e conferência"
    return {"pratos_escolhidos": sorted(expected), "primeira_ficha_salva": first["id"],
            "cozinha_a_confirmar": True, "saldo_complementos": 80, "compras_ou_reservas": False}


def check_new_search(conversation: Conversation, home: Path, store) -> dict:
    """Depois das sugestões: outra busca não repete o que ela viu, e escolher uma opção encerra a busca."""
    from sabor_da_maria import escolhas, jornada

    before, stock = state(home), store.ingredientes()
    first = escolhas.opcoes_exibidas(conversation.turn(jornada.resumo(store)["mensagem"]))
    assert first, "a primeira busca não mostrou opções de receita para escolher"
    assert state(home) == before, "receber sugestões criou uma ficha ou movimentou outros dados"
    text = conversation.turn(NOVA_BUSCA)
    second = escolhas.opcoes_exibidas(text)
    assert state(home) == before, "pedir outra busca criou uma ficha ou movimentou outros dados"
    seen = {fold(o["nome"]) for o in first} | {o["fonte_url"] for o in first}
    repeated = [o["nome"] for o in second if fold(o["nome"]) in seen or o["fonte_url"] in seen]
    assert not repeated, "a nova busca reapresentou receitas já vistas: " + "; ".join(repeated)
    assert second or "nao encontrei outra receita" in fold(text), "a nova busca terminou sem opções novas e sem avisar"
    assert text.count("?") == 1, "a nova busca apresentou várias perguntas ao mesmo tempo"
    calls = Counter(e.get("tool") for e in conversation.turns[-1]["ferramentas"] if e.get("status") == "completed")
    assert calls["web_search"] and not set(calls) - {"despensa_listar", "despensa_buscar", "web_search", "web_extract"}, \
        "a nova busca não pesquisou ou usou ferramentas de gravação"
    # A lista mais recente é a que os botões do chat oferecem; sem novidade, a anterior continua valendo.
    chosen = (second or first)[0]
    answer = conversation.turn(f"Escolho a opção {chosen['numero']}.")
    data = state(home)
    assert [(d["nome"], d["status"], d["preco_venda"]) for d in data["pratos"]] == [(chosen["nome"], "selecionado", None)], \
        "a escolha não guardou só a opção apontada, como rascunho"
    assert jornada.resumo(store)["etapa"] != "pesquisa", "com a escolha feita a jornada continuou pedindo outra busca"
    recipe = next((r for r in data["receitas"] if r["id"] == data["pratos"][0].get("receita_id")), None)
    assert recipe and (recipe["dados"].get("fonte_verificada") or {}).get("url") == chosen["fonte_url"], \
        "a ficha da opção escolhida não foi montada com a fonte apresentada"
    for table in ("reservas", "compras", "carrinho", "vendas", "analises_preco"):
        assert data[table] == before[table], f"a escolha alterou {table} sem autorização"
    assert store.ingredientes() == stock and store.carrinho_resumo()["saldo"] == 80
    assert "#None" not in answer and "ValueError" not in answer, "a resposta expôs referência ou erro interno"
    # A ficha fala do que falta em palavras para ela; quem recebe a orientação com os campos é o modelo.
    missing_price = [i["ingrediente"] for i in recipe["dados"]["resultado"]["itens"] if i.get("falta_preco")]
    first_block = (recipe["dados"].get("bloqueios") or [{}])[0]
    assert first_block.get("tipo") not in ("fonte_incompleta", "fonte_divergente") or recipe["dados"]["fidelidade_fonte"]["diferencas"], \
        "a ficha copiada da fonte foi dada como divergente sem diferença nenhuma"
    follow_up = None
    # Só quando a pergunta da ficha é sobre o preço: "esse ponto" precisa ser o preço que falta.
    if first_block.get("tipo") == "ingrediente_pendente" and "preco de compra" in fold(first_block.get("detalhe", "")):
        assert "preco de compra" in fold(answer), "a ficha não disse que falta o preço de compra"
        follow_up = conversation.turn("Sim, vamos resolver esse ponto.")
        calls = Counter(e.get("tool") for e in conversation.turns[-1]["ferramentas"] if e.get("status") == "completed")
        assert calls["web_search"] or re.search(r"\b(?:quanto|preco|valor|custa|paga)\b", fold(follow_up)), \
            "com um preço faltando, a consultora não pesquisou nem perguntou"
        data = state(home)
        assert all(d["status"] != "aceito" for d in data["pratos"]) and not data["reservas"], "resolver uma pendência virou aprovação"
        assert store.carrinho_resumo()["saldo"] == 80, "resolver uma pendência gastou o saldo sem confirmação"
        # Cotar um complemento não muda a receita: a ficha continua com todos os ingredientes da fonte.
        latest = next(r for r in data["receitas"] if r["id"] == data["pratos"][0]["receita_id"])
        assert len(latest["dados"]["ingredientes"]) == len(recipe["dados"]["ingredientes"]), "a cotação tirou ingredientes da ficha"
        assert not latest["dados"]["fidelidade_fonte"]["diferencas"], "a cotação fez a ficha divergir da fonte"
    leaked = [m[0] for turn in conversation.turns for m in [TERMOS_INTERNOS.search(turn["resposta"])] if m]
    assert not leaked, "a conversa mostrou à pessoa um termo interno: " + "; ".join(leaked)
    return {"primeiras_opcoes": [o["nome"] for o in first], "novas_opcoes": [o["nome"] for o in second],
            "repetidas": 0, "prato_escolhido": chosen["nome"], "ficha_montada": True,
            "ingredientes_sem_preco": missing_price, "pendencia_de_preco_conduzida": follow_up is not None,
            "termos_internos_na_conversa": 0, "saldo_complementos": 80, "compras_ou_reservas": False}


def check_resume_source(conversation: Conversation, home: Path, store) -> dict:
    """A receita incorreta já está salva; retomar deve reparar e fazer progresso."""
    from sabor_da_maria import jornada

    before, stock = state(home), store.ingredientes()
    history = [{"role": "assistant", "content": "A ficha diverge da receita lida: meia cebola foi salva como meio quilo e faltou azeite. Vamos resolver só esse ponto antes de aprovar?"}]
    text = conversation.turn(RETOMAR_BATATA, history=history)
    after = state(home)
    assert len(after["pratos"]) == 1 and after["pratos"][0]["id"] == before["pratos"][0]["id"], "a retomada criou outro prato"
    recipe = next(r for r in after["receitas"] if r["id"] == after["pratos"][0]["receita_id"])
    assert before["receitas"][0] in after["receitas"], "a correção apagou a versão anterior da receita"
    assert not recipe["dados"]["fidelidade_fonte"]["diferencas"], "a IA perguntou pela cozinha sem corrigir a fonte"
    ingredients = recipe["dados"]["ingredientes"]
    assert len(ingredients) == len(FONTE_BATATA["ingredientes"]), "a correção omitiu ingredientes publicados"
    onion = next(i for i in ingredients if "cebola" in fold(i["nome"]))
    water = next(i for i in ingredients if fold(i["nome"]) == "agua")
    assert onion["quantidade"] == 0.5 and onion["unidade"] == "un", "meia cebola ainda foi interpretada como meio quilo"
    assert water["quantidade"] == 0.5 and "xicara" in water["unidade"], "meia xícara ainda virou meio litro"
    assert all("quantidade" not in i for i in ingredients if fold(i["nome"]) in ("sal", "salsinha", "pimenta calabresa", "paprica doce", "azeite a gosto")), "inventou medidas de temperos sem publicação"
    assert "tempo" in fold(text) and text.count("?") == 1, "retomada não fez apenas a primeira pergunta real"
    assert "pronto para ser aprovado" not in fold(text), "declarou prato pronto com pendências"
    assert not after["perfil"], "retomada virou confirmação da cozinha"
    repeat = conversation.turn(RETOMAR_BATATA)
    assert repeat.count("?") == 1 and "tempo" in fold(repeat), "o mesmo CTA ignorou novamente a pergunta atual"
    assert len(state(home)["receitas"]) == len(after["receitas"]), "repetir CTA criou outra versão sem mudança"
    opcoes_tempo = jornada.resumo(store)["pergunta"]["opcoes"]
    assert [o["rotulo"] for o in opcoes_tempo] == ["< 30 min", "1 hora", "> 2 horas", "> 6 horas"], "faltam opções de tempo no chat"
    uma_hora = next(o for o in opcoes_tempo if o["rotulo"] == "1 hora")
    answer = conversation.turn(uma_hora["mensagem"])
    current = jornada.resumo(store)
    assert store.perfil()["restricoes"]["tempo_por_cozinhada_min"] == 60, "não salvou a resposta à pergunta"
    assert not any(b["tipo"] == "tempo_disponivel_pendente" for b in current["pendencias"]), "a etapa respondida continuou pendente"
    assert answer.count("?") == 1, "a resposta trouxe várias perguntas simultâneas"
    final = state(home)
    for table in ("reservas", "compras", "carrinho", "vendas", "analises_preco"):
        assert final[table] == before[table], f"retomada alterou {table} sem confirmação"
    assert store.ingredientes() == stock and store.carrinho_resumo()["saldo"] == 80
    assert final["pratos"][0]["status"] == "selecionado" and final["pratos"][0]["preco_venda"] is None
    return {"fonte_reparada": True, "cta_repetido_sem_duplicar": True, "resposta_avancou_etapa": True,
            "saldo_complementos": 80, "compras_ou_reservas": False}


def check_sales(conversation: Conversation, home: Path, store) -> dict:
    before = state(home)
    assert not before["vendas"], "fixture já contém vendas"
    dish = before["pratos"][0]
    budget = store.carrinho_resumo()
    conversation.turn(f"Vendi 2 porções do prato #{dish['id']}, {dish['nome']}, hoje pelo preço do cardápio. Registre a venda realizada.")
    after = state(home)
    assert len(after["vendas"]) == 1, "a venda informada não foi registrada uma única vez"
    sale = after["vendas"][0]
    assert sale["prato_id"] == dish["id"] and sale["quantidade"] == 2 and sale["preco_unitario"] == 6.50
    assert sale["status"] == "confirmada"
    assert {k: v for k, v in before.items() if k != "vendas"} == {k: v for k, v in after.items() if k != "vendas"}, "registrar a venda alterou planejamento ou reservas"
    assert store.carrinho_resumo() == budget, "a venda alterou o orçamento de complementos"
    from sabor_da_maria import vendas
    summary = vendas.resumo(store)["periodos"]["dia"]
    assert summary["faturamento_bruto"] == 13 and summary["taxa_plataforma"] == 1.30
    assert summary["recebe_liquido"] == 11.70 and summary["cmv_total"] == 3.50 and summary["lucro_estimado"] == 8.20
    conversation.turn("Consulte as vendas realizadas de hoje e me mostre o faturamento e o lucro estimado.")
    assert state(home)["vendas"] == after["vendas"], "consultar vendas gravou outro evento"
    tools = {event.get("tool") for turn in conversation.turns for event in turn["ferramentas"]}
    assert {"vendas_registrar", "vendas_consultar"} <= tools, "a IA não usou os registros reais de vendas"
    return {"vendas_registradas": 1, "quantidade": 2, "faturamento": 13, "saldo_complementos": budget["saldo"], "orcamento_e_reservas_preservados": True}


def check_pending(case: str, conversation: Conversation, home: Path) -> dict:
    data = state(home)
    dish = data["pratos"][0]
    message = (f"Sim, aceito preparar o prato #{dish['id']}, {dish['nome']}, mas não tenho forno."
               if case == "aceite-com-ressalva" else
               f"Quero preparar o prato #{dish['id']}, {dish['nome']}. "
               "Confirme o que falta antes de aprovar. Não compre nem reserve nada.")
    text = conversation.turn(message)
    data = state(home)
    assert not data["reservas"] and not data["analises_preco"], "uma pendência real foi aprovada"
    assert not data["carrinho"] and not data["compras"], "a conferência de pendências criou compras não solicitadas"
    assert all(d["status"] != "aceito" for d in data["pratos"]), "prato pendente foi registrado"
    words = {"cozinha-incompleta": ("forno",), "aceite-com-ressalva": ("forno",),
             "embalagem-sem-peso": ("peso", "grama", "embalagem"),
             "orcamento-insuficiente": ("orcamento", "saldo", "80,00")}[case]
    assert any(word in fold(text) for word in words), "a resposta não explicou a pendência concreta"
    if case == "embalagem-sem-peso":
        assert re.search(r"\b(peso|gramas?|volume|ml)\b", fold(text)), "a pergunta não trata da medida da embalagem"
    if case == "aceite-com-ressalva":
        assert data["perfil"]["equipamentos"].get("forno") is False, "a negativa atual não foi persistida sobre o cadastro antigo"
        assert re.search(r"sem forno|nao(?:\W+\w+){0,5}\W+forno|forno(?:\W+\w+){0,5}\W+nao", fold(text)), "a resposta não explicou a indisponibilidade do forno"
    return {"pendencia_explicada": True, "pratos_aprovados": 0, "reservas": 0}


def check_complete(conversation: Conversation, home: Path, store, *, guided: bool = False) -> dict:
    if guided:
        from sabor_da_maria import escolhas, jornada

        antes = state(home)
        stock = conversation.turn(CONTINUE_REQUEST)
        assert jornada.resumo(store)["passo"] == "estoque", "cadastro saltou a apresentação do estoque"
        assert "pronta" in stock.lower(), "não perguntou pela prontidão"
        assert state(home) == antes and not conversation.turns[-1]["ferramentas"], "cadastro iniciou ferramentas ou compromissos antes da escolha"
        conversation.turn("Agora não. Quero olhar meu estoque com calma.")
        assert jornada.resumo(store)["controle"]["pausado"], "a pausa não ficou salva"
        assert state(home) == antes, "a pausa alterou estoque, fichas ou compras"
        pronta = conversation.turn("Sim, estou pronta para começar a montar meus pratos.")
        assert jornada.resumo(store)["passo"] == "ajuda", "sim de prontidão não levou à escolha de ajuda"
        assert state(home) == antes and not conversation.turns[-1]["ferramentas"], "prontidão virou montagem ou aceite"
        assert "pesquisar" in pronta.lower() and "prefere" in pronta.lower(), "não ofereceu as duas escolhas"
        sugestoes = conversation.turn(jornada.resumo(store)["pergunta"]["opcoes"][0]["mensagem"])
        assert state(home) == antes, "receber sugestões criou uma ficha ou movimentou outros dados"
        opcoes = escolhas.opcoes_exibidas(sugestoes)
        assert opcoes, "a pesquisa não mostrou opções de receita para escolher"
        text = conversation.turn(f"Escolho {opcoes[0]['numero']}")
        assert any(d["nome"] == opcoes[0]["nome"] for d in state(home)["pratos"]), "a ficha escolhida não foi guardada"
    else:
        text = conversation.turn(REQUEST)
    data = state(home)
    recipe = visible_recipe(data, text)
    guided_answers = 0
    if guided:
        from sabor_da_maria import jornada
        # A persona sintética tem os equipamentos/habilidades, mas só declarou
        # parte deles. Responde apenas ao requisito que a consultora perguntar,
        # pelo mesmo contrato dos botões do chat; não completa o catálogo todo.
        while recipe is None and guided_answers < 3:
            step = jornada.resumo(store)
            question = step.get("pergunta")
            if step["etapa"] != "pendencias" or not question:
                break
            assert question["opcoes"], "a pergunta guiada não oferece uma resposta"
            text = conversation.turn(question["opcoes"][0]["mensagem"])
            guided_answers += 1
            data = state(home)
            recipe = visible_recipe(data, text)
            if recipe is None:
                current = jornada.pratos_atuais(store)
                viable = next((d for d in current if d.get("veredito") == "viavel" and
                               (d["nome"].lower() in text.lower() or f"#{d['id']}" in text)), None)
                recipe = next((r for r in data["receitas"] if viable and r["id"] == viable.get("receita_id")), None)
    if recipe is None and not guided:
        text = conversation.turn("Pode propor uma adaptação dessa receita com o que tenho no estoque?")
        data, recipe = state(home), None
        recipe = visible_recipe(data, text)
    assert recipe is not None, "nenhuma versão viável foi salva e apresentada antes do aceite"
    dish = next(d for d in data["pratos"] if d.get("receita_id") == recipe["id"])
    assert not data["reservas"], "houve reserva antes da decisão de preço"
    fidelity = assert_contract(recipe, source_snapshots(home), "\n".join(t["resposta"] for t in conversation.turns))
    acceptance = ("Sim, gosto de cozinhar e não vejo impedimento. Aceito preparar essa versão."
                  if guided else f"Sim, gosto de cozinhar e não vejo impedimento. Aceito preparar {recipe['nome']} "
                  "nessa versão conferida; mostre os cenários de preço.")
    text = conversation.turn(acceptance)
    data = state(home)
    analysis = next((a for a in data["analises_preco"] if a["prato_id"] == dish["id"]), None)
    assert analysis, "aceite não produziu análise de preço da versão escolhida"
    prices = analysis["dados"]
    assert prices["taxa_plataforma"] == 0.10 and len(prices["cenarios"]) in (2, 3)
    from sabor_da_maria.costing import brl
    assert all(brl(s["preco_sugerido"]) in text for s in prices["cenarios"]), "os cenários mostrados diferem dos salvos"
    price = prices["cenarios"][1]["preco_sugerido"]
    conversation.turn(f"Escolho vender por R$ {price:.2f} por porção. Pode registrar."
                      if guided else f"Escolho vender o prato #{dish['id']} por R$ {price:.2f} por porção. Registre no cardápio.")
    data = state(home)
    dish = next(d for d in data["pratos"] if d["id"] == dish["id"])
    assert dish["status"] == "aceito" and dish["preco_venda"] == price, "preço escolhido não foi registrado"
    expected = {i["item_id"]: float(i["usa_da_despensa_base"]) for i in recipe["dados"]["resultado"]["itens"]
                if i.get("item_id") and i.get("usa_da_despensa_base", 0) > 0}
    actual = {r["ingrediente_id"]: float(r["quantidade_base"]) for r in data["reservas"] if r["prato_id"] == dish["id"]}
    assert set(actual) == set(expected) and all(abs(actual[k] - expected[k]) < 1e-8 for k in expected), "reservas diferem da ficha"
    assert all(d["id"] == dish["id"] or d["status"] != "aceito" for d in data["pratos"]), "outro prato foi aprovado"
    tools = {e.get("tool") for t in conversation.turns for e in t["ferramentas"]}
    assert {"web_search", "web_extract"} <= tools, "pesquisa real não foi executada"
    budget = store.orcamento()["restante"]
    if not recipe["dados"]["resultado"]["compras"]:
        assert budget == 80, "o uso do estoque debitou o orçamento de complementos"
    if guided:
        before_marketing = state(home)
        conversation.turn("Não quero planejar divulgação agora.")
        assert jornada.resumo(store)["controle"]["divulgacao"] == "dispensada", "a decisão sobre publicidade não ficou salva"
        assert state(home) == before_marketing and store.orcamento()["restante"] == budget, "publicidade alterou compras ou pratos"
    return {"prato": dish["nome"], "status": dish["status"], "preco_escolhido": price,
            "cmv_lote": dish["cmv_total"], "reservas": len(actual), "saldo_complementos": budget,
            "respostas_guiadas": guided_answers, "fidelidade": fidelity,
            **({"etapas_guiadas": 5, "pausa_preservada": True, "divulgacao": "dispensada"} if guided else {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caso", choices=("completo", "conducao", "vendas", "escolha-dois", "nova-busca", "retomada-fonte", "cozinha-incompleta", "embalagem-sem-peso", "orcamento-insuficiente", "aceite-com-ressalva", "adaptacao-flexivel"), default="completo")
    parser.add_argument("--saida", type=Path, default=None)
    parser.add_argument("--max-turns", type=int, default=None, help="Sobrescrita apenas no gateway isolado; omitido usa a configuração de produção.")
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    output = (args.saida or Path(tempfile.mkdtemp(prefix="sabor-evidencia-"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = "sabor-avaliacao-" + secrets.token_hex(4)
    summary = {"ok": False, "caso": args.caso, "dados_reais_utilizados": False}
    with tempfile.TemporaryDirectory(prefix="sabor-runtime-") as temporary:
        root, key = Path(temporary), secrets.token_urlsafe(24)
        home, internals = root / "home", root / "internals"
        for directory in (home / "workspace", home / "sabor_da_maria", home / "memories", internals):
            directory.mkdir(parents=True, exist_ok=True)
        for filename in ("SOUL.md", "config.yaml"):
            shutil.copyfile(REPO / "hermes-home" / filename, home / filename)
        shutil.copyfile(REPO / "hermes-home/workspace/AGENTS.md", home / "workspace/AGENTS.md")
        config = (home / "config.yaml").read_text()
        if args.max_turns is not None:
            if args.max_turns < 1:
                parser.error("max-turns precisa ser positivo")
            config, count = re.subn(r"(?m)^(\s*)max_turns:\s*\d+\s*$", rf"\g<1>max_turns: {args.max_turns}", config)
            assert count == 1, "configuração max_turns não foi localizada"
            (home / "config.yaml").write_text(config)
        summary["max_turns"] = int(re.search(r"max_turns:\s*(\d+)", config)[1])
        summary["configuracao_sha256"] = hashlib.sha256(config.encode()).hexdigest()
        store = fixture(home, args.caso)
        conversation = None
        try:
            image = run("docker", "inspect", "--format", "{{.Config.Image}}", "sabor-hermes")
            summary["imagem_hermes"] = image
            try:
                inference = run("docker", "port", "sabor-inference", "8000/tcp").splitlines()[0]
                with urllib.request.urlopen("http://" + inference + "/kv-cache", timeout=3) as response:
                    summary["limites_inferencia"] = json.load(response).get("generation_limits")
            except (OSError, ValueError, subprocess.SubprocessError):
                summary["limites_inferencia"] = None
            network = run("docker", "inspect", "--format", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}", "sabor-hermes")
            command = ["docker", "run", "-d", "--rm", "--name", name, "--network", network, "-p", "127.0.0.1::8642"]
            mounts = ((home, "/opt/data"), (internals, "/database-internals"),
                      (REPO / "hermes-home/plugins", "/opt/data/plugins:ro"),
                      (REPO / "hermes-home/skills", "/opt/data/skills:ro"),
                      (REPO / "data", "/opt/data/sabor_da_maria/entrada:ro"),
                      (REPO / "prompts", "/prompts:ro"), (REPO / "docs", "/documentos:ro"))
            for source, target in mounts:
                command.extend(("-v", f"{source}:{target}"))
            environment = {"HERMES_UID": str(os.getuid()), "HERMES_GID": str(os.getgid()), "HERMES_HOME": "/opt/data",
                           "SEARXNG_URL": "http://searxng:8080", "SABOR_DA_MARIA_INTERNALS": "/database-internals",
                           "SABOR_DA_MARIA_DB": "/opt/data/sabor_da_maria/sabor_da_maria.db", "SABOR_DA_MARIA_ORCAMENTO": "80",
                           "SABOR_DA_MARIA_XLSX": "/opt/data/sabor_da_maria/entrada/despensa_dona_maria.xlsx",
                           "SABOR_DA_MARIA_BASE_CONHECIMENTO": "/prompts/base-de-conhecimento", "API_SERVER_ENABLED": "true",
                           "API_SERVER_HOST": "0.0.0.0", "API_SERVER_KEY": key}
            for key_name, value in environment.items():
                command.extend(("-e", key_name + "=" + value))
            run(*command, image, "gateway", "run")
            base = "http://" + run("docker", "port", name, "8642/tcp").splitlines()[0]
            for _ in range(90):
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("gateway isolado não ficou saudável")
            conversation = Conversation(base, key, home, output, args.timeout)
            summary.update(check_flexible_adaptation(conversation, home, store) if args.caso == "adaptacao-flexivel" else
                           check_sales(conversation, home, store) if args.caso == "vendas" else
                           check_two_choices(conversation, home, store) if args.caso == "escolha-dois" else
                           check_new_search(conversation, home, store) if args.caso == "nova-busca" else
                           check_resume_source(conversation, home, store) if args.caso == "retomada-fonte" else
                           check_complete(conversation, home, store, guided=args.caso == "conducao") if args.caso in ("completo", "conducao")
                           else check_pending(args.caso, conversation, home))
            summary["ok"] = True
        except Exception as exc:
            summary["erro"] = str(exc)
        finally:
            logs = subprocess.run(["docker", "logs", "--tail", "100", name], capture_output=True, text=True)
            (output / "gateway.log").write_text((logs.stdout + logs.stderr).replace(key, "[chave sintética removida]"))
            for logfile in (home / "logs").glob("*.log"):
                trecho = "\n".join(logfile.read_text(errors="replace").splitlines()[-200:])
                (output / ("runtime-" + logfile.name)).write_text(trecho.replace(key, "[chave sintética removida]"))
            if conversation:
                summary["turnos"] = [{k: v for k, v in t.items() if k not in ("pergunta", "resposta", "ferramentas")}
                                     for t in conversation.turns]
            if (home / "sabor_da_maria/sabor_da_maria.db").exists():
                write_json(output / "estado.json", state(home))
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
            summary["runtime_temporario_removido"] = cleanup.returncode == 0 or "No such container" in cleanup.stderr
            if not summary["runtime_temporario_removido"]:
                summary["ok"] = False
                summary["erro_limpeza"] = "Não foi possível confirmar a remoção do container " + name
            write_json(output / "resumo.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("Evidências: " + str(output), flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
