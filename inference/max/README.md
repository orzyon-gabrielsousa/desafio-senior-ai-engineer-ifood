# Perfil experimental: Modular MAX + Qwen3.5-4B

Serviço `inference-max` do `docker-compose.yml` (perfil `max`). Sobe no lugar do llama.cpp:

```bash
make up-max      # para o llama.cpp e sobe o MAX
make down-max    # volta ao llama.cpp
```

E no `hermes-home/config.yaml` troque:

```yaml
model:
  default: "Qwen/Qwen3.5-4B"
  streaming: false     # MAX não faz streaming com function calling
```

## Por que é experimental (estado em novembro de 2026)

| Ponto | Situação |
|---|---|
| RTX 50 (sm_120) | é alvo de compilação, mas `max serve` em Blackwell de consumo ainda tem épico aberto (modular/modular#6570) e relatos de falha com Qwen3-8B numa RTX 5090 (#5707). FP8 cai em kernel lento; bf16 funciona. |
| Driver | bug de PTX-JIT nos drivers 580/595, corrigido no 615.71.09. Esta máquina está no 595.99. |
| Tool calling | o MAX só tem parser para Qwen3.5/3.6 (`qwen3_5`), GLM, Gemma 4, Kimi e DeepSeek. O Qwen3 "clássico" não tem parser: o `<tool_call>` vira texto e o Hermes não aceita tool call em texto. |
| Streaming | sem streaming com function calling → `model.streaming: false` no Hermes. |
| Campos extras | o MAX rejeita campos desconhecidos no request → `--allow-extra-request-fields`. |
| Modelo | único Qwen com tool calling que cabe em 16 GB: `Qwen/Qwen3.5-4B` em bf16 (~9,3 GB de pesos + KV híbrido DeltaNet ~2 GB em 64k). Sem GPTQ/GGUF para Qwen no MAX. |

## Roteiro do spike (1 hora)

1. `docker pull modular/max-nvidia-full:26.7.0` (imagem grande).
2. `make up-max` e acompanhar `docker compose logs -f inference-max` até o servidor responder em `/v1/health`.
3. Repetir o passo 2 do `scripts/smoke_test.sh` (request com `tools`) apontando `MODEL_NAME=Qwen/Qwen3.5-4B`: precisa voltar `tool_calls`.
4. Se passar, `make ask Q="..."` com o Hermes configurado para o modelo. Se falhar por kernel/driver, registrar o erro aqui e seguir com o llama.cpp.

## Resultado do spike (2026-11-13)

Imagem `modular/max-nvidia-full:26.7.0` (≈2 GB) e pesos `Qwen/Qwen3.5-4B` (8,8 GB, bf16) baixados; `max serve` sobe, detecta `Qwen3_5ForConditionalGeneration`, aceita a RTX 5070 Ti e planeja memória (com `--device-memory-utilization 0.80` o planejador recusou: "model 8.68 GiB ... don't leave room for KV cache"; com `0.95` e `--max-batch-size 1` ele aceitou, limitando `max_seq_len` a 39 168).

**Bloqueio: RAM do host, não GPU.** Na fase "Building, compiling, and initializing model" o worker do MAX passou de 16 GB de RSS e o kernel o matou (`Out of memory: Killed process ... anon-rss:16649372kB`, `max.serve.process_control.SubprocessExit: Killed`), em duas tentativas seguidas. A máquina tem 30 GB de RAM com ~15 GB ocupados pelo desktop e outros projetos, então sobravam ~12 GB.

O que ainda não foi testado, portanto: os kernels em sm_120 e o parser de tool calling do Qwen3.5. Para repetir o spike: liberar ≥20 GB de RAM (ou adicionar swap), `make up-max`, acompanhar `docker compose --profile max logs -f inference-max` e rodar a sonda de `tool_calls` do `scripts/smoke_test.sh` com `MODEL_NAME=Qwen/Qwen3.5-4B`. Observação: o `max_seq_len` planejado (39 168) ficou abaixo dos 64k que o Hermes exige, então mesmo funcionando seria preciso `model.context_length: 65536` fixado e cuidado com conversas longas.

Dois detalhes de integração descobertos e já corrigidos no `docker-compose.yml`: o entrypoint da imagem já é `max serve` (o `command` leva só os argumentos) e o planejador de memória conta a VRAM total, não a livre, então `--device-memory-utilization` precisa descontar o que o desktop usa.
