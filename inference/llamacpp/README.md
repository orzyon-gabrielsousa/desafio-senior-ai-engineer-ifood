# Motor padrão: llama.cpp + Qwen3-8B (GGUF)

Serviço `inference-llamacpp` do `docker-compose.yml` (perfil `llamacpp`, padrão).

| Item | Valor |
|---|---|
| Imagem | `ghcr.io/ggml-org/llama.cpp:server-cuda13` (CUDA 13, suporta RTX 50 / sm_120) |
| Modelo | `unsloth/Qwen3-8B-GGUF` → `Qwen3-8B-Q4_K_M.gguf` (5,0 GB) — `make model` |
| Contexto | 65 536 tokens (o Hermes recusa modelos abaixo de 64k) |
| KV cache | q8_0 (K e V), flash attention ligada |
| Modo | sem "thinking" (`--reasoning-budget 0`); troque para `-1` no `.env` se quiser raciocínio |
| Tool calling | nativo: `--jinja` usa o template do Qwen3 (formato Hermes `<tool_call>` → `tool_calls` OpenAI) |
| Amostragem | temp 0,7 · top-p 0,8 · top-k 20 · min-p 0 (recomendação da Qwen para modo não-pensante) |

## Por que essas escolhas

- **Qwen3-8B**: melhor relação qualidade/VRAM com tool calling confiável em português. Quantização Q4_K_M deixa ~11 GB em uso (pesos + KV de 64k), o que cabe nos 16 GB da RTX 5070 Ti dividindo a placa com o desktop. Com a GPU livre, `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`) melhora a qualidade.
- **GGUF da unsloth**: traz o chat template corrigido para tool calls no llama.cpp.
- **64k de contexto com KV q8_0**: o KV cache em f16 custaria ~9,7 GB sozinho; em q8_0 fica ~5 GB sem perda perceptível.

## Alternativas testadas na pesquisa

- **vLLM 0.33 (CUDA 13)** com `--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3` e `Qwen/Qwen3-8B-FP8`: funciona na RTX 50, mas a imagem é bem maior e o uso de VRAM menos previsível.
- **Ollama**: funciona, mas o contexto padrão é 4k (precisa `num_ctx`), não há `tool_choice` e o streaming com tools trava no Hermes (`model.streaming: false`).
- **Modular MAX**: ver `../max/README.md`.
