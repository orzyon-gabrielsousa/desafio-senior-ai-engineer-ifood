# Motor padrão: llama.cpp + Qwen3-8B (GGUF)

Serviço `inference-llamacpp` do `docker-compose.yml` (perfil `llamacpp`, padrão).

| Item | Valor |
|---|---|
| Imagem | `ghcr.io/ggml-org/llama.cpp:server-cuda13` fixada no Compose (CUDA 13, suporta RTX 50 / sm_120) |
| Modelo | `unsloth/Qwen3-8B-GGUF` → `Qwen3-8B-Q4_K_M.gguf` (5,0 GB) — `make model` |
| Contexto | 65 536 tokens (o Hermes recusa modelos abaixo de 64k) |
| KV cache | q8_0 (K) e q4_0 (V), flash attention ligada |
| Modo | sem "thinking" (`--reasoning-budget 0`); troque para `-1` no `.env` se quiser raciocínio |
| Teto de resposta | 4 096 tokens por geração (`LLAMA_N_PREDICT`, `-1` desliga): impede geração sem fim, que já derrubou o servidor com `CUDA error: the launch timed out` na GPU dividida com o desktop. Vale para quem não manda `max_tokens` (o Hermes não manda); um pedido com `max_tokens` maior ultrapassa o teto |
| Tool calling | nativo: `--jinja` usa o template do Qwen3 (formato Hermes `<tool_call>` → `tool_calls` OpenAI) |
| Amostragem | temp 0,7 · top-p 0,8 · top-k 20 · min-p 0 (recomendação da Qwen para modo não-pensante) |

## Por que essas escolhas

- **Qwen3-8B**: melhor relação qualidade/VRAM com tool calling confiável em português. Quantização Q4_K_M deixa ~11 GB em uso (pesos + KV de 64k), o que cabe nos 16 GB da RTX 5070 Ti dividindo a placa com o desktop. Com a GPU livre, `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`) melhora a qualidade.
- **GGUF da unsloth**: traz o chat template corrigido para tool calls no llama.cpp.
- **64k de contexto com KV quantizado**: o KV cache em f16 custaria ~9,7 GB sozinho. Quantizar K e V reduz esse uso para caber na GPU.

## Cache KV no SSD

O llama.cpp guarda os tensores de atenção da geração na VRAM. Depois de uma geração e com o slot livre, o serviço guarda o cache do slot em `database-internals/kv/`. Mantém uma cópia, identificada pela versão do runtime, pelo arquivo do modelo e pelos parâmetros de inferência. Na próxima subida, restaura apenas uma cópia compatível. O motor reaproveita o prefixo de tokens em comum quando processa a próxima solicitação. A memória da consultora vem do histórico e da busca de contexto.

`LLAMA_KV_SSD_INTERVAL` controla o intervalo mínimo entre gravações (30 segundos por padrão). Para desligar o snapshot no SSD, use `LLAMA_KV_SSD_INTERVAL=0` no `.env`. O modo experimental MAX usa outro servidor e não participa desse mecanismo.

`curl -fsS http://127.0.0.1:8010/kv-cache` mostra o tamanho do snapshot e os números de tokens salvos e restaurados. A API administrativa de slots só atende dentro do próprio contêiner. O [manual da versão fixada do llama.cpp](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/server/README.md#post-slotsid_slotactionsave-save-the-prompt-cache-of-the-specified-slot-to-a-file) descreve o formato das chamadas de save e restore.

## Alternativas testadas na pesquisa

- **vLLM 0.33 (CUDA 13)** com `--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3` e `Qwen/Qwen3-8B-FP8`: funciona na RTX 50, mas a imagem é bem maior e o uso de VRAM menos previsível.
- **Ollama**: funciona, mas o contexto padrão é 4k (precisa `num_ctx`), não há `tool_choice` e o streaming com tools trava no Hermes (`model.streaming: false`).
- **Modular MAX**: ver `../max/README.md`.
