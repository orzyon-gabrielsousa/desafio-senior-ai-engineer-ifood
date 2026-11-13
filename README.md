# Sabor da Maria — consultora de cardápio e precificação com Hermes Agent

Solução do desafio técnico **Senior AI Engineer** (iFood): o [Hermes Agent](https://github.com/nousresearch/hermes-agent) instalado e customizado como consultora de cardápio da Dona Maria, que está abrindo o delivery *Sabor da Maria*. O agente pesquisa receitas reais na web, descobre o que ela consegue produzir (utensílios, técnicas, restrições), compara os ingredientes com a despensa e o orçamento de R$ 80, calcula o CMV e propõe cenários de preço com a taxa de 10% da plataforma. Ela decide.

Tudo roda **localmente, em Docker, sem nenhuma chave de API**: modelo de linguagem (llama.cpp + Qwen3-8B na GPU), busca na web (SearXNG), extração de receitas (provider próprio) e banco de dados (SQLite embutido).

> Enunciado completo em [`docs/desafio-senior-ai-engineer.md`](docs/desafio-senior-ai-engineer.md). Decisões de arquitetura com alternativas descartadas em [`docs/decisoes.md`](docs/decisoes.md).

## Arquitetura

```
┌────────────────────────────── docker compose ───────────────────────────────┐
│                                                                              │
│  você ──TUI (make chat)──▶ ┌──────────────────────────────────────────────┐  │
│  React/curl ──:8642──────▶ │ hermes  (nousresearch/hermes-agent:v0.23.1)  │  │
│                            │  HERMES_HOME = ./hermes-home (versionado)    │  │
│                            │   ├ SOUL.md          persona da consultora   │  │
│                            │   ├ workspace/AGENTS.md  regras do negócio   │  │
│                            │   ├ skills/…/consultoria-cardapio  playbook  │  │
│                            │   ├ memories/        MEMORY.md + USER.md     │  │
│                            │   ├ plugins/sabor_da_maria  10 tools + extrator │
│                            │   └ sabor_da_maria/sabor_da_maria.db (SQLite)│  │
│                            └───────┬───────────────────┬──────────────────┘  │
│                        OpenAI API  │                   │ /search?format=json │
│                                    ▼                   ▼                     │
│           ┌──────────────────────────────┐   ┌───────────────────────┐       │
│           │ inference (llama.cpp, GPU)   │   │ searxng (metabusca)   │       │
│           │ Qwen3-8B Q4_K_M · 64k YaRN   │   │ google/bing/brave/…   │       │
│           │ tool calling nativo          │   └───────────────────────┘       │
│           │ [perfil max: Modular MAX]    │          web_extract ──▶ sites de  │
│           └──────────────────────────────┘          receita (schema.org)     │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Peça | O que é | Por quê |
|---|---|---|
| **Hermes Agent** | produto; nada no core alterado | é o que o desafio avalia |
| **SOUL.md** | identidade e tom da consultora | sempre no prompt (slot 1) |
| **AGENTS.md** | regras do negócio, fluxo e "regras de ouro" | contexto do projeto carregado da pasta de trabalho |
| **Skill `consultoria-cardapio`** | playbook passo a passo com exemplos de chamadas | pré-carregada em `make chat`, consultável via `skill_view` |
| **Plugin `sabor_da_maria`** | 10 tools (despensa, perfil, conferência, CMV, preço, cardápio) + provider `receitas-local` de `web_extract` | lógica determinística fora do modelo; só stdlib |
| **SQLite** | despensa normalizada, perfil da cozinha, receitas, pratos, compras | banco embutido, persistente entre sessões |
| **llama.cpp + Qwen3-8B** | servidor OpenAI-compatible com `tool_calls` nativos, 64k de contexto | roda na RTX 5070 Ti com folga; MAX fica como perfil experimental |
| **SearXNG** | busca web local, JSON, pt-BR | zero chaves, zero SaaS |
| **clarify + memory** | tools nativas do Hermes | elicitação com múltipla escolha; memória entre conversas |

## Como o fluxo do desafio foi implementado

| Etapa | Onde está | Como funciona |
|---|---|---|
| **2.1 Pesquisa de receitas** | `web_search` (SearXNG) + `web_extract` (provider `receitas-local`) | o extrator lê schema.org/Recipe (JSON-LD) e devolve ingredientes, rendimento, tempo e passos; sem JSON-LD, cai para o texto do `<article>` |
| **2.2 Elicitação de restrições** | `perfil_cozinha_consultar` → `clarify` → `perfil_cozinha_salvar` | o plugin mantém um checklist (bocas do fogão, forno, pressão, air fryer, liquidificador, geladeira, tempo, gás, energia, técnicas); o que falta vira pergunta com opções; técnicas específicas da receita entram em `tecnicas_necessarias` |
| **2.3 Ingredientes** | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | casamento aproximado com sinônimos (cheiro-verde→salsinha, fubá→polenta...), conversão de medidas caseiras (colher, xícara, dente, "1 cebola média") com premissas explícitas, o que tem / falta / custo / cabe no orçamento, veredito `viavel` · `precisa_confirmar` · `inviavel_por_enquanto` |
| **2.4 CMV e preço** | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV = Σ quantidade usada × custo unitário (preço pago ÷ quantidade comprada, abas cruzadas), incluindo compras complementares, com memória de cálculo linha a linha; preço mínimo = CMV ÷ 0,90; 3 cenários de margem com a conta aberta e simulação de preços que ela sugerir; o registro só acontece com o preço escolhido por ela |

Detalhes que a planilha esconde e o plugin trata: unidades compostas (`balde 2kg`, `un 500g`, `un 500ml`) normalizadas para kg/L; a **cobertura de chocolate** vem como `1 un` sem peso, então a tool sinaliza e o agente pergunta o peso do pote antes de usar em receita medida em gramas.

## Rodando

Requisitos: Docker + Compose v2, driver NVIDIA ≥ 580 com NVIDIA Container Toolkit, GPU com 16 GB (ou ajuste o modelo), ~8 GB de disco para imagens e 5 GB para o modelo. Testado em Linux, RTX 5070 Ti, driver 595.

```bash
make setup     # cria .env (UID/GID do host) e pastas
make model     # baixa Qwen3-8B-Q4_K_M.gguf (5 GB) para ./models
make up        # sobe inferência (GPU), SearXNG e Hermes
make smoke     # smoke test ponta a ponta (tool calling, busca, plugin, resposta do agente)
make chat      # TUI do Hermes com o playbook pré-carregado — a demo
```

Outros atalhos: `make ask Q="..."` (pergunta única), `make validate` (`hermes plugins validate`, toolsets, tamanho do prompt), `make test` (26 testes unitários do plugin, só stdlib), `make reset-dados` (zera perfil/cardápio), `make logs`, `make down`.

Primeira subida: o llama.cpp leva ~30 s para carregar o modelo; o Hermes inicia o gateway (API server em `http://localhost:8642`, chave `API_SERVER_KEY` do `.env`).

### Roteiro de demo (5–10 min)

1. "Oi, sou a Dona Maria, quero montar o cardápio com o que tenho na despensa." → o agente lê a despensa e pergunta o estilo.
2. "Gosto de fazer frango e carne moída, para marmita." → busca receitas reais, extrai e apresenta 2–3 com fonte; pede feedback.
3. Escolhida a receita, o agente abre o `clarify`: bocas do fogão, forno, pressão, air fryer, liquidificador, geladeira, tempo, gás, energia; depois técnicas da receita.
4. Conferência de ingredientes: o que ela tem, o que falta, custo, cabe nos R$ 80? Se um item é vendido por unidade sem peso, ele pergunta.
5. Aceite → CMV com memória de cálculo → preço mínimo e 3 cenários → ela escolhe → cardápio registrado e orçamento abatido.
6. Fechar com `cardapio_consultar` e mostrar que a memória guardou equipamentos e decisões (nova sessão já sabe).

## Estrutura do repositório

```
.
├── docker-compose.yml        inference-llamacpp (padrão) · inference-max (experimental) · searxng · hermes
├── .env.example              portas, modelo, UID/GID, chave do API server
├── Makefile                  setup · model · up · smoke · chat · test · validate · reset-dados
├── data/despensa_dona_maria.xlsx   entrada do desafio (montada só-leitura no container)
├── hermes-home/              = $HERMES_HOME do container (só a customização é versionada)
│   ├── config.yaml           modelo local, web backends, toolsets enxutos, plugin habilitado
│   ├── SOUL.md               persona
│   ├── workspace/AGENTS.md   contexto do projeto (regras, fluxo, formato)
│   ├── skills/sabor-da-maria/consultoria-cardapio/SKILL.md
│   ├── memories/MEMORY.md, USER.md   sementes de memória
│   └── plugins/sabor_da_maria/
│       ├── plugin.yaml       manifesto (10 tools, 1 web provider, zero dependências)
│       ├── __init__.py       register(ctx)
│       ├── tools.py          handlers + schemas (OpenAI function format)
│       ├── store.py          SQLite (despensa, perfil, receitas, pratos, compras)
│       ├── xlsx_reader.py    leitor .xlsx em stdlib
│       ├── units.py          unidades da planilha + medidas caseiras + densidades
│       ├── matching.py       sinônimos + casamento aproximado
│       ├── costing.py        CMV, preço mínimo, cenários, simulação
│       ├── extract.py        provider receitas-local (urllib + JSON-LD Recipe + texto)
│       └── tests/            26 testes (unittest)
├── searxng/settings.yml      JSON ligado, pt-BR, engines barulhentos removidos
├── inference/llamacpp/README.md · inference/max/README.md
├── scripts/smoke_test.sh
└── docs/decisoes.md · docs/desafio-senior-ai-engineer.md
```

## Configuração do Hermes em uma tela

```yaml
model:    { default: qwen3-8b, provider: custom, base_url: http://inference:8000/v1, context_length: 65536 }
web:      { search_backend: searxng, extract_backend: receitas-local, keyless_fallback: false }
plugins:  { enabled: [sabor_da_maria] }
tools:    { tool_search: { enabled: "off" } }
platform_toolsets:
  cli:        [web, clarify, memory, skills, todo, sabor_da_maria]
  api_server: [web, memory, skills, todo, sabor_da_maria]
```

O toolset é enxuto de propósito: ~18 tools em vez das ~40 do bundle `hermes-cli`. Um modelo de 8B escolhe melhor com menos opções, e a consultora não precisa (nem deve ter) terminal, arquivos ou browser. O `tool_search` (meta-tool que esconde as tools de plugin até o modelo procurá-las) fica desligado: com ele ligado o Qwen3-8B não via as tools da despensa e respondia perguntas de custo com `web_search`. Sem `keyless_fallback`, nenhuma requisição sai para SaaS mesmo se um backend falhar.

## Modelo e desempenho medidos

| Item | Valor (RTX 5070 Ti 16 GB, driver 595) |
|---|---|
| Modelo | `unsloth/Qwen3-8B-GGUF` Q4_K_M, modo não-pensante |
| Contexto | 65 536 tokens (YaRN ×2 sobre 32 768), KV cache K q8_0 / V q4_0 |
| VRAM | ≈12,5 GB em uso no total com o desktop (≈9 GB do servidor) |
| Velocidade | ≈135 tokens/s de geração, ≈3 000 tokens/s de prompt |
| Latência | pergunta com 1 tool: 1–3 s · busca + extração + conferência de ingredientes (6 tool calls): ≈16 s · busca + extração de 2 páginas com resumo: ≈25 s |

Para mais qualidade com a GPU livre: `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` no `.env` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`). Para raciocínio explícito do Qwen3: `LLAMA_REASONING_BUDGET=-1`.

### Modular MAX (perfil `max`, experimental)

`make up-max` sobe `modular/max-nvidia-full:26.7.0` com `Qwen/Qwen3.5-4B` no lugar do llama.cpp. Em novembro de 2026 o `max serve` em Blackwell de consumo (sm_120) ainda está em validação pela Modular, só o Qwen3.5 tem parser de tool calling e não há streaming com function calling; por isso é perfil opcional, não o padrão. No spike feito nesta máquina o servidor subiu e planejou a memória da GPU, mas o worker foi morto pelo OOM killer do host durante a compilação do modelo (>16 GB de RAM); o relato completo e como repetir estão em [`inference/max/README.md`](inference/max/README.md).

## Testes e validação

```bash
make test        # 26 testes: leitor xlsx, unidades/conversões, casamento, SQLite, CMV/preço, fluxo das tools, extrator
make validate    # hermes plugins validate (manifesto, registros, colisões, security scan) + toolsets + prompt-size
make smoke       # /health, tool_calls nativos, SearXNG JSON, plugin habilitado, resposta do agente com a despensa
```

## Limitações conhecidas

- Um modelo de 8B às vezes afirma o que a Dona Maria "já tem" antes de conferir; as regras em `AGENTS.md` e o veredito das tools reduzem isso, mas na demo vale pedir "confere na despensa".
- Conversões de medidas caseiras são estimativas (densidade, peso de "1 cebola"); a tool marca cada uma e o agente deve repassar as que pesam no custo.
- O SearXNG depende de buscadores públicos; se um engine bloquear, os outros cobrem. Sem internet não há pesquisa de receitas.
- A tool `clarify` é da TUI; via API server (8642) a elicitação acontece em texto.

## Próximos passos

- Frontend React consumindo `http://localhost:8642/v1/chat/completions` (CORS já configurado) ou o webapp do Hermes (`webapp.enabled: true` + login básico no `.env`).
- Spike do perfil MAX na RTX 50 e comparação com o llama.cpp no mesmo roteiro.
- Bônus em Mojo: matcher de ingredientes exposto ao Python via `PythonModuleBuilder`.
