# Motor padrão: llama.cpp + Qwen3-8B (GGUF)

Serviço `inference-llamacpp` do `docker-compose.yml` (perfil `llamacpp`, padrão).

| Item | Valor |
|---|---|
| Imagem | `ghcr.io/ggml-org/llama.cpp:server-cuda13` fixada no Compose (CUDA 13, suporta RTX 50 / sm_120) |
| Modelo | `unsloth/Qwen3-8B-GGUF` → `Qwen3-8B-Q4_K_M.gguf` (5,0 GB) — `make model` |
| Contexto | 65 536 tokens (o Hermes recusa modelos abaixo de 64k) |
| KV cache | q8_0 (K) e q4_0 (V), flash attention ligada |
| Modo | sem thinking: `--reasoning off`, `enable_thinking:false` no template e orçamento de raciocínio zero |
| Saída por geração | Padrão de 1 536 tokens (`LLAMA_N_PREDICT`), com teto efetivo de 2 048 (`LLAMA_MAX_OUTPUT_TOKENS`) aplicado pelo proxy. Pedidos menores são preservados; `max_tokens`, `max_completion_tokens`, `max_output_tokens` e `n_predict` não ultrapassam esse teto |
| Tool calling | nativo: `--jinja` usa o template do Qwen3 (formato Hermes `<tool_call>` → `tool_calls` OpenAI) |
| Amostragem | temp 0,7 · top-p 0,8 · top-k 20 · min-p 0 (recomendação da Qwen para modo não-pensante) |

## Por que essas escolhas

- **Qwen3-8B**: melhor relação qualidade/VRAM com tool calling confiável em português. Quantização Q4_K_M deixa ~11 GB em uso (pesos + KV de 64k), o que cabe nos 16 GB da RTX 5070 Ti dividindo a placa com o desktop. Com a GPU livre, `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`) melhora a qualidade.
- **GGUF da unsloth**: traz o chat template corrigido para tool calls no llama.cpp.
- **64k de contexto com KV quantizado**: o KV cache em f16 custaria ~9,7 GB sozinho. Quantizar K e V reduz esse uso para caber na GPU.

## Limites de geração e trabalho em segundo plano

O `--n-predict` nativo é um padrão que o cliente pode sobrescrever. O proxy restringe somente o orçamento de saída das rotas de geração; preserva mensagens, ferramentas, streaming e marcadores de parada. Limites conflitantes usam o menor valor, e corpos ou tipos inválidos são recusados com HTTP 400. Uma resposta que chega ao teto pode terminar com `finish_reason: length`; a validação ponta a ponta deve conferir que a ficha e as chamadas de ferramenta continuam completas.

Os limites ativos aparecem em `/kv-cache` no campo `generation_limits`. Para aumentar o orçamento, ajuste `LLAMA_MAX_OUTPUT_TOKENS` e, se necessário, `LLAMA_N_PREDICT` e recrie o serviço. O modo de raciocínio é explícito; para experimentar thinking, ajuste em conjunto `LLAMA_REASONING_MODE`, `LLAMA_ENABLE_THINKING` e `LLAMA_REASONING_BUDGET`, mantendo o teto total de saída.

No Hermes, `auxiliary.background_review.enabled: false` desativa somente os forks automáticos de revisão de memória/skills. Eles compartilham a mesma GPU e podem gerar milhares de tokens depois de uma conversa. O histórico no SQLite, a memória explícita, a indexação de embeddings e a recuperação de conversas permanecem ativos. `/refine` continua disponível manualmente. A opção é nativa da versão instalada, sem alterações no núcleo do Hermes.

## Cache KV no SSD

O llama.cpp guarda os tensores de atenção da geração na VRAM. Depois de uma geração e com o slot livre, o serviço guarda o cache do slot em `database-internals/kv/`. Mantém uma cópia, identificada pela versão do runtime, pelo arquivo do modelo e pelos parâmetros de inferência. Na próxima subida, restaura apenas uma cópia compatível. O motor reaproveita o prefixo de tokens em comum quando processa a próxima solicitação. A memória da consultora vem do histórico e da busca de contexto.

`LLAMA_KV_SSD_INTERVAL` controla o intervalo mínimo entre gravações (30 segundos por padrão). Para desligar o snapshot no SSD, use `LLAMA_KV_SSD_INTERVAL=0` no `.env`. O modo experimental MAX usa outro servidor e não participa desse mecanismo.

`curl -fsS http://127.0.0.1:8010/kv-cache` mostra o tamanho do snapshot e os números de tokens salvos e restaurados. A API administrativa de slots só atende dentro do próprio contêiner. O [manual da versão fixada do llama.cpp](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/server/README.md#post-slotsid_slotactionsave-save-the-prompt-cache-of-the-specified-slot-to-a-file) descreve o formato das chamadas de save e restore.

## Alternativas testadas na pesquisa

- **vLLM 0.33 (CUDA 13)** com `--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3` e `Qwen/Qwen3-8B-FP8`: funciona na RTX 50, mas a imagem é bem maior e o uso de VRAM menos previsível.
- **Ollama**: funciona, mas o contexto padrão é 4k (precisa `num_ctx`), não há `tool_choice` e o streaming com tools trava no Hermes (`model.streaming: false`).
- **Modular MAX**: ver `../max/README.md`.
