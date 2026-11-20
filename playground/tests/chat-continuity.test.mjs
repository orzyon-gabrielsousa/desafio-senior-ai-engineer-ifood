import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: 'export * from "./src/lib/chat"; export { asConversation } from "./src/lib/storage";',
    resolveDir: fileURLToPath(new URL("..", import.meta.url)),
  },
  bundle: true,
  write: false,
  format: "esm",
});
const { ContextoLongoError, MENSAGEM_CONTEXTO_LONGO, streamTurn, asConversation } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString("base64")}`
);
const alerta = "This conversation has grown too long for qwen3-8b to read, and Hermes couldn't shrink it enough automatically.";

function fluxo(...dados) {
  return new Response(dados.map((dado) => `data: ${JSON.stringify(dado)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream", "X-Hermes-Session-Id": "sessao-recebida" },
  });
}

function delta(texto) {
  return { choices: [{ delta: { content: texto } }] };
}

function manipuladores(sobrescrever = {}) {
  return {
    sessionId: "sessao-anterior",
    messages: [{ role: "user", content: "Vamos continuar o prato #2." }],
    signal: new AbortController().signal,
    onSession() {}, onDelta() {}, onTool() {},
    ...sobrescrever,
  };
}

test("trata alerta de contexto fragmentado e duplicado como falha, sem mostrar texto bruto", async (t) => {
  t.mock.method(globalThis, "fetch", async () => fluxo(delta(alerta.slice(0, 18)), delta(alerta.slice(18)), delta(alerta)));
  const partes = [];
  await assert.rejects(streamTurn(manipuladores({ onDelta: (parte) => partes.push(parte) })), ContextoLongoError);
  assert.deepEqual(partes, []);
});

test("classifica o código de limite no HTTP, JSON e SSE", async (t) => {
  const erro = { error: { code: "context_length_exceeded", message: "Exceeded" } };
  const respostas = [Response.json(erro, { status: 400 }), Response.json(erro), fluxo(erro)];
  t.mock.method(globalThis, "fetch", async () => respostas.shift());
  for (let i = 0; i < 3; i += 1) await assert.rejects(streamTurn(manipuladores()), ContextoLongoError);
});

test("restaura a conversa antiga sem duplicar avisos nem descartar as demais mensagens", () => {
  const conversa = asConversation({ id: "1", sessionId: "sessao-antiga", messages: [
    { id: "a", role: "user", content: "Vamos montar a ficha do prato #2." },
    { id: "b", role: "assistant", content: `${alerta}\n\n${alerta}` },
  ] });
  assert.equal(conversa.sessionId, "sessao-antiga");
  assert.equal(conversa.messages.length, 2);
  assert.equal(conversa.messages[1].content, MENSAGEM_CONTEXTO_LONGO);
  assert.equal(conversa.messages[1].error, true);
});

test("retoma a ficha em sessão nova com contexto limitado e preserva preferência anterior", async (t) => {
  const pedidos = [];
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    pedidos.push({ headers: init.headers, ...JSON.parse(init.body) });
    return fluxo(delta("Ficha conferida."));
  });
  const mensagens = [
    { role: "user", content: "Quero margem de 40%." },
    ...Array.from({ length: 20 }, () => ({ role: "assistant", content: "x".repeat(4500) })),
    { role: "assistant", content: alerta },
    { role: "user", content: "Vamos continuar o prato #2." },
  ];
  const ids = [];
  await streamTurn(manipuladores({ messages: mensagens, sessionId: null, onSession: (id) => ids.push(id) }));
  await streamTurn(manipuladores({ messages: mensagens, sessionId: null }));
  const [pedido, outro] = pedidos;
  assert.equal(pedido.headers["X-Hermes-Session-Id"], undefined);
  assert.notEqual(pedido.messages[0].content, outro.messages[0].content);
  assert.deepEqual(ids, ["sessao-recebida"]);
  assert.equal(pedido.messages[1].content, "Quero margem de 40%.");
  assert.match(pedido.messages.at(-1).content, /^Vamos continuar o prato #2\./);
  assert.ok(pedido.messages.slice(1, -1).map(({ content }) => content).join("").length < 13_000);
  assert.ok(pedido.messages.every(({ content }) => !content.includes(alerta)));
  assert.equal(mensagens.length, 23);
});

test("mantém a continuidade normal e passa deltas sem alteração", async (t) => {
  const partes = [];
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    assert.equal(init.headers["X-Hermes-Session-Id"], "sessao-anterior");
    assert.equal(JSON.parse(init.body).messages.length, 1);
    return fluxo(delta("Ficha "), delta("conferida."));
  });
  await streamTurn(manipuladores({ onDelta: (parte) => partes.push(parte) }));
  assert.equal(partes.join(""), "Ficha conferida.");
});
