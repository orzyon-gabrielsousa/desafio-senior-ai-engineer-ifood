#!/usr/bin/env bash
# Smoke test ponta a ponta do Sabor da Maria. Rode com `make run` ou `make up` no ar.
set -euo pipefail
cd "$(dirname "$0")/.."

ok()   { printf '\033[32m[ok]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[falhou]\033[0m %s\n' "$*"; exit 1; }
MODEL_NAME="${MODEL_NAME:-$(grep -E '^MODEL_NAME=' .env 2>/dev/null | cut -d= -f2 | cut -d'#' -f1 | tr -d ' ' || true)}"
MODEL_NAME="${MODEL_NAME:-qwen3-8b}"
PORT="${INFERENCE_HOST_PORT:-$(grep -E '^INFERENCE_HOST_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)}"
PORT="${PORT:-8010}"

echo "0) o playground acessa o Hermes e o SQLite pelo nginx"
PLAYGROUND_ADDR=$(docker compose port playground 80) || fail "playground sem porta publicada (make run)"
for rota in /hermes/health /api/saude /api/estado /api/cozinha/pratos /api/analytics/analises; do
  curl -fsS --max-time 10 "http://${PLAYGROUND_ADDR}${rota}" >/dev/null || fail "${rota} falhou pelo playground (docker compose logs playground api hermes)"
done
ok "chat e dados locais acessíveis pelo playground"

echo "1) inferência responde em /health (porta ${PORT})"
curl -fsS "http://localhost:${PORT}/health" >/dev/null || fail "inferência não respondeu (docker compose logs inference-llamacpp)"
ok "inferência de pé"

echo "2) inferência devolve tool_calls nativos (exigência do Hermes)"
RESP=$(curl -fsS "http://localhost:${PORT}/v1/chat/completions" -H 'Content-Type: application/json' -d @- <<EOF
{"model": "${MODEL_NAME}", "temperature": 0,
 "messages": [{"role": "user", "content": "Quanto custa o arroz na despensa? Use a ferramenta."}],
 "tools": [{"type": "function", "function": {"name": "despensa_buscar", "description": "Procura ingredientes na despensa",
   "parameters": {"type": "object", "properties": {"ingredientes": {"type": "array", "items": {"type": "string"}}}, "required": ["ingredientes"]}}}],
 "tool_choice": "auto"}
EOF
)
echo "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); m=d["choices"][0]["message"]; tc=m.get("tool_calls") or []; assert tc, m; print("   tool_call:", tc[0]["function"]["name"], tc[0]["function"]["arguments"])' \
  || fail "o servidor não devolveu tool_calls; o Hermes não funciona sem isso"
ok "tool calling nativo"

echo "3) SearXNG devolve JSON"
N=$(curl -fsS "http://localhost:8888/search?q=receita+de+frango+com+batata&format=json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["results"]))')
[ "$N" -gt 0 ] || fail "SearXNG sem resultados (engines bloqueados? veja docker compose logs searxng)"
ok "busca local: $N resultados"

echo "4) Hermes carrega o plugin e o toolset está ativo na CLI"
docker compose exec -T hermes hermes plugins list --plain --no-bundled | grep -E "^enabled" | grep -q "sabor_da_maria" || fail "plugin sabor_da_maria não está habilitado (plugins.enabled no config.yaml)"
docker compose exec -T hermes hermes tools list | grep -E "enabled.*sabor_da_maria" >/dev/null || fail "toolset sabor_da_maria não está habilitado para a CLI (platform_toolsets.cli)"
ok "plugin habilitado e toolset ativo"

echo "5) Hermes responde a uma pergunta usando a despensa (modelo local)"
OUT=$(docker compose exec -T hermes hermes chat -q "Quanto custa 1 kg de arroz na despensa da Dona Maria? Responda em uma frase." 2>&1 | tail -20)
echo "$OUT" | sed 's/^/   /'
echo "$OUT" | grep -Eqi "4,98|4\.98" || fail "resposta não trouxe o custo unitário do arroz (R$ 4,98/kg)"
ok "ponta a ponta com tools"

echo
ok "smoke test completo"
