# Sabor da Maria — consultora de cardápio e precificação com Hermes Agent

Solução do desafio técnico **Senior AI Engineer** (iFood): o [Hermes Agent](https://github.com/nousresearch/hermes-agent) instalado e customizado como consultora de cardápio da Dona Maria, que está abrindo o delivery *Sabor da Maria*. O agente pesquisa receitas reais na web, descobre o que ela consegue produzir (utensílios, técnicas, restrições), compara os ingredientes com a despensa e o orçamento de R$ 80, calcula o CMV e propõe cenários de preço com a taxa de 10% da plataforma. Ela decide.

Tudo roda **localmente, em Docker, sem nenhuma chave de API**: modelo de linguagem (llama.cpp + Qwen3-8B na GPU), busca na web (SearXNG), extração de receitas (provider próprio) e banco de dados (SQLite embutido). A conversa com a Dona Maria acontece no playground em React (`http://localhost:3000`) ou na TUI (`make chat`). No playground ela também monta o **carrinho de compras** dentro do saldo de R$ 80 (cada prato aberto, com os ingredientes e o valor de cada um, e uma aba de pratos e ingredientes **salvos**), e a consultora usa o **perfil do negócio** dela (restaurante de hotel, bolos, pizzas, e onde fica a loja) e o **Analytics** de pratos mais vendidos por zona, cidade, estado, região e país para sugerir pratos.

> Enunciado completo em [`docs/desafio-senior-ai-engineer.md`](docs/desafio-senior-ai-engineer.md). Decisões de arquitetura com alternativas descartadas em [`docs/decisoes.md`](docs/decisoes.md).

## Arquitetura

```
┌──────────────────────────── docker compose ────────────────────────────┐
│                                                                        │
│  navegador :3000 ──▶ playground (React + nginx)                        │
│                         │  /hermes  (proxy, chave só no servidor)       │
│                         │  /api     (proxy) ──▶ api: carrinho, salvos, │
│                         │                         perfil, analytics    │
│  TUI (make chat) ───────┼──────────────────────┐                       │
│                         ▼                      │                       │
│              ┌────────────────────────────────┴─────────────────────┐ │
│              │ hermes (nousresearch/hermes-agent:v0.23.1)            │ │
│              │  HERMES_HOME = ./hermes-home                         │ │
│              │   ├ SOUL.md              persona da consultora        │ │
│              │   ├ workspace/AGENTS.md  regras do negócio            │ │
│              │   ├ skills/…/consultoria-cardapio  playbook           │ │
│              │   ├ memories/            MEMORY.md + USER.md          │ │
│              │   ├ plugins/sabor_da_maria   18 tools+hooks+extrator  │ │
│              │   ├ sabor_da_maria.db    SQLite                       │ │
│              │   └ /prompts (ro)  base de conhecimento do Analytics  │ │
│              └────────────┬───────────────────────┬──────────────────┘ │
│                OpenAI API │                       │ /search?format=json│
│                           ▼                       ▼                    │
│        ┌────────────────────────────┐   ┌──────────────────────┐      │
│        │ inference (llama.cpp, GPU) │   │ searxng (metabusca)  │      │
│        │ Qwen3-8B Q4_K_M · 64k YaRN │   │ google/bing/brave/…  │      │
│        │ tool calling nativo        │   └──────────────────────┘      │
│        │ [perfil max: Modular MAX]  │        web_extract ──▶ receitas │
│        └────────────────────────────┘        (schema.org/Recipe)      │
└────────────────────────────────────────────────────────────────────────┘
```

| Peça | O que é | Por quê |
|---|---|---|
| **Hermes Agent** | produto; nada no core alterado | é o que o desafio avalia |
| **SOUL.md** | identidade e tom da consultora | sempre no prompt (slot 1) |
| **AGENTS.md** | regras do negócio, fluxo e "regras de ouro" | contexto do projeto carregado da pasta de trabalho |
| **Skill `consultoria-cardapio`** | playbook passo a passo com exemplos de chamadas | pré-carregada em `make chat`, consultável via `skill_view` |
| **Plugin `sabor_da_maria`** | 18 tools (despensa, perfil, conferência, CMV, preço, cardápio, carrinho com prato aberto, itens salvos, Analytics) + 3 hooks (falas da Dona Maria, painel do carrinho) + provider `receitas-local` de `web_extract` | lógica determinística e travas fora do modelo; só stdlib |
| **SQLite** | despensa normalizada, perfil da cozinha, receitas, pratos, compras, carrinho (com pratos abertos), pratos e ingredientes salvos, perfil do negócio (com o lugar da loja) | banco embutido, persistente entre sessões |
| **Base de conhecimento** | `prompts/base-de-conhecimento/pratos-mais-vendidos/`: Markdown por região e estado, com zona, cidade, estado, região e país; pratos mais vendidos, preço médio e ingredientes típicos (**dados de exemplo**, não são vendas reais) | a consultora (`analytics_consultar`) e a tela leem o mesmo arquivo; trocar por dados reais é editar tabela, sem código |
| **API da tela** | serviço `api`: Python stdlib na própria imagem do Hermes, usando a mesma `Store` e o mesmo leitor de Analytics do plugin, porta 8700 só no loopback | a tela edita carrinho, pratos, salvos e perfil do negócio e lê o Analytics sobre os mesmos dados que a consultora; nenhuma regra duplicada, nada novo para baixar |
| **llama.cpp + Qwen3-8B** | servidor OpenAI-compatible com `tool_calls` nativos, 64k de contexto | roda na RTX 5070 Ti com folga; MAX fica como perfil experimental |
| **SearXNG** | busca web local, JSON, pt-BR | zero chaves, zero SaaS |
| **Playground** | React + nginx, porta 3000 | chat da consultora, carrinho com saldo ao vivo (pratos abertos e aba Salvos), perfil do negócio e Analytics; o proxy injeta a chave, o JS não a vê |
| **clarify + memory** | tools nativas do Hermes | elicitação com múltipla escolha na TUI; memória entre conversas |

## Como o fluxo do desafio foi implementado

| Etapa | Onde está | Como funciona |
|---|---|---|
| **2.1 Pesquisa de receitas** | `web_search` (SearXNG) + `web_extract` (provider `receitas-local`) | o extrator lê schema.org/Recipe (JSON-LD) e devolve ingredientes, rendimento, tempo e passos; sem JSON-LD, cai para o texto do `<article>` |
| **2.2 Elicitação de restrições** | `perfil_cozinha_consultar` → `clarify` → `perfil_cozinha_salvar` | o plugin mantém um checklist (bocas do fogão, forno, pressão, air fryer, liquidificador, geladeira, tempo, gás, energia, técnicas); o que falta vira pergunta com opções; técnicas específicas da receita entram em `tecnicas_necessarias` |
| **2.3 Ingredientes** | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | casamento aproximado com sinônimos (cheiro-verde→salsinha, fubá→polenta...), conversão de medidas caseiras (colher, xícara, dente, "1 cebola média") com premissas explícitas, o que tem / falta / custo / cabe no orçamento, veredito `viavel` · `precisa_confirmar` · `inviavel_por_enquanto` |
| **2.4 CMV e preço** | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV = Σ quantidade usada × custo unitário (preço pago ÷ quantidade comprada, abas cruzadas), incluindo compras complementares, com memória de cálculo linha a linha; preço mínimo = CMV ÷ 0,90; 3 cenários de margem com a conta aberta e simulação de preços que ela sugerir; o registro só acontece com o preço escolhido por ela. As três tools recusam (`bloqueado: true` + `bloqueios`) enquanto houver requisito sem resposta; o registro reserva o estoque e abate o orçamento numa operação só |

Detalhes que a planilha esconde e o plugin trata: unidades compostas (`balde 2kg`, `un 500g`, `un 500ml`) normalizadas para kg/L; a **cobertura de chocolate** vem como `1 un` sem peso, então a tool sinaliza e o agente pergunta o peso do pote antes de usar em receita medida em gramas.

**Travas no backend.** O veredito da conferência (`viavel` · `precisa_confirmar` · `inviavel_por_enquanto`) não é só aviso: `prato_calcular_cmv`, `preco_sugerir` e `cardapio_registrar` recusam com `bloqueado: true` e a lista de `bloqueios` (forno não informado, técnica que ela disse não dominar, ingrediente sem preço, compra que não cabe nos R$ 80) enquanto o estado salvo não estiver `viavel`, mesmo que o modelo escreva na conversa que está tudo certo. O registro reavalia tudo contra o estado atual, recusa se CMV ou compras mudaram desde a apresentação (o preço foi escolhido em cima de outros números) e, quando aceita, reserva na despensa o que o prato usa e compromete as compras no orçamento numa transação só. Candidatas não consomem estoque; o segundo prato enxerga `disponivel = estoque − reservas`. Repetir o registro não duplica nada; `cardapio_remover` libera reserva e orçamento.

**Trava de evidência no perfil.** Numa prova com o modelo real o Qwen3-8B gravou no perfil da cozinha técnicas e utensílios que a Dona Maria nunca citou (copiando até os valores de exemplo do schema da tool). Agora `perfil_cozinha_salvar` só grava o que ela disse: dois hooks públicos de plugin (`pre_llm_call` e `post_tool_call` do `clarify`) guardam as falas dela por sessão (`falas.py`), e cada item enviado precisa ter base nelas (a fala cita o item ou um sinônimo e, se o valor é número, o número também aparece; "tenho sim" conta junto com a pergunta a que respondeu). O que não tem base volta em `recusados` e não entra; o perfil fica incompleto e o agente tem de perguntar. Limite assumido: a trava impede **gravar o que ela não falou**, não confere se o sim/não bate com a fala (isso é para os evals). Os valores de exemplo saíram do schema.

**Carrinho, saldo e perfil do negócio.** O carrinho é a lista de compras dela dentro do orçamento: `saldo = orçamento − compromissos de pratos aceitos − carrinho`, e item que não cabe no saldo é recusado (na tela e nas tools) com quanto falta. A tela (barra do topo, painel do carrinho, "Meu negócio", Analytics) fala com o serviço `api`; a consultora usa as tools `carrinho_consultar`, `carrinho_adicionar` (ingrediente avulso), `carrinho_adicionar_prato`, `carrinho_remover` e `carrinho_montar_da_receita` (esta última só com receita `viavel`). Como um modelo de 8B esquece de chamar tool, um hook injeta em todo turno um painel curto (saldo, carrinho, salvos, perfil e local do negócio, os três mais vendidos do lugar), então ela sabe o que a Dona Maria mexeu na tela sem perguntar ("você já pegou a couve, o saldo é R$ 68,00"). Quando o prato é aceito, os itens dele saem do carrinho e viram compras comprometidas, para o mesmo gasto não contar duas vezes.

**Prato aberto e itens salvos.** Prato nunca entra no carrinho como um item só com preço fechado ("coxinha, R$ 18"): ele entra aberto, com cada ingrediente, a quantidade e o valor, mais o total do prato. O que ela já tem na despensa aparece com o valor e o selo "já tem", e **não gasta saldo** (só o que ela compra conta). `carrinho_adicionar` recusa nome de prato (os salvos e os da base de pratos mais vendidos) e manda abrir o prato; ingrediente sem valor ou prato sem valor por ingrediente nem entram. Pratos e ingredientes avulsos podem ser **salvos** (aba Salvos na tela, tool `salvos_salvar`) e voltam ao carrinho de um toque ou pelo nome, com os mesmos valores. A migração é aditiva (`ALTER TABLE` só quando falta a coluna), então um banco antigo continua valendo.

**Analytics e base de conhecimento.** `prompts/base-de-conhecimento/pratos-mais-vendidos/` guarda, em Markdown, os pratos mais vendidos com preço médio, faixa, índice de vendas e ingredientes típicos, por **zona, cidade, estado, região e país** (uma pasta por região, um arquivo por estado, um por região e um do país; formato e como trocar por dados reais no README da pasta). O lugar da loja (estado, cidade e zona em "Meu negócio") prioriza o ranking: a zona pesa mais que a cidade, que pesa mais que o estado, a região e o país, de modo que o que lidera perto da loja sobe. A consultora lê pela tool `analytics_consultar` e pelas três primeiras posições do painel do turno; a tela mostra o ranking completo, com abas por nível, filtro por categoria e o botão "Montar com a consultora", que pede para ela abrir o prato com os ingredientes e os valores. **Os números da pasta são ilustrativos, criados para a demonstração: não são vendas reais.** Cada arquivo, a tela e a frase que a consultora repete dizem isso.

## Rodando

Requisitos: Docker + Compose v2, driver NVIDIA ≥ 580 com NVIDIA Container Toolkit, GPU com 16 GB (ou ajuste o modelo), ~8 GB de disco para imagens e 5 GB para o modelo. Testado em Linux, RTX 5070 Ti, driver 595.

```bash
make setup     # cria .env (UID/GID do host) e pastas
make model     # baixa Qwen3-8B-Q4_K_M.gguf (5 GB) para ./models
make up        # sobe inferência (GPU), SearXNG, Hermes, a API do carrinho e o playground
make smoke     # smoke test ponta a ponta (tool calling, busca, plugin, resposta do agente)
make chat      # TUI do Hermes com o playbook pré-carregado
```

O playground abre em [http://localhost:3000](http://localhost:3000) junto com `make up`. É o chat da consultora, no visual do chat do iFood (logo, vermelho `#ea1d2c`, balões e sugestões em chips; só ambienta a demo): a Dona Maria escreve, a resposta chega em fluxo, e cada ferramenta (despensa, receita, CMV, preço) aparece como um passo da cozinha. A barra do topo mostra orçamento, carrinho e saldo ao vivo; um toque abre o carrinho (pratos abertos com o valor de cada ingrediente, ingredientes avulsos e a aba Salvos; a Dona Maria põe, tira e salva, ou pede para a consultora fazer isso). No menu, "Meu negócio" guarda o perfil e o lugar da loja, e "Analytics" mostra os pratos mais vendidos priorizados por esse lugar. A chave da API fica no nginx do container; o navegador só fala com a mesma origem (`/hermes/` e `/api/`). Para mexer na interface com recarga rápida, `make playground-dev` (Node 20+ na máquina, Hermes já no ar).

Outros atalhos: `make ask Q="..."` (pergunta única), `make validate` (`hermes plugins validate`, toolsets, tamanho do prompt), `make test` (132 testes unitários do plugin, só stdlib), `make reset-dados` (zera perfil da cozinha, cardápio, carrinho, salvos e perfil do negócio; a planilha é recarregada), `make restart` (Hermes e API, depois de editar o plugin), `make logs`, `make down`.

Primeira subida: o llama.cpp leva ~30 s para carregar o modelo; o Hermes inicia o gateway (API server em `http://localhost:8642`, chave `API_SERVER_KEY` do `.env`).

### Roteiro de demo (5–10 min)

1. "Oi, sou a Dona Maria, quero montar o cardápio com o que tenho na despensa." → o agente lê a despensa e pergunta o estilo.
2. "Gosto de fazer frango e carne moída, para marmita." → busca receitas reais, extrai e apresenta 2–3 com fonte; pede feedback.
3. Escolhida a receita, o agente abre o `clarify`: bocas do fogão, forno, pressão, air fryer, liquidificador, geladeira, tempo, gás, energia; depois técnicas da receita.
4. Conferência de ingredientes: o que ela tem, o que falta, custo, cabe nos R$ 80? Se um item é vendido por unidade sem peso, ele pergunta.
5. Carrinho: em "Meu negócio" (menu) ela escolhe restaurante de hotel, arroz e feijão, bolos e pizzas e onde fica a loja (estado, cidade, zona); "me sugere pratos que vendem bem" → a consultora consulta o Analytics e propõe os mais vendidos da zona e da cidade (avisando que a base é de exemplo); escolhido o prato, ela o põe no carrinho **aberto, com cada ingrediente e o valor dele**, e a barra do topo mostra orçamento, carrinho e saldo ao vivo. "Coloca uma coxinha por R$ 18" é recusado como item único e vira o prato aberto. "Salva esse prato" o guarda na aba Salvos. Pedir uma lagosta de R$ 95 mostra a recusa por saldo ("faltam R$ 27,00"); tirar um item pela tela ou pelo chat devolve o saldo. O menu "Analytics" mostra o mesmo ranking na tela, por nível (zona, cidade, estado, região, país).
6. Aceite → CMV com memória de cálculo → preço mínimo e 3 cenários → ela escolhe → cardápio registrado, estoque reservado e orçamento abatido (os itens da receita saem do carrinho e viram compras comprometidas). Se o agente tentar pular a elicitação, a tool recusa e devolve o que falta perguntar.
7. Fechar com `cardapio_consultar` e mostrar que a memória guardou equipamentos e decisões (nova sessão já sabe).

## Estrutura do repositório

```
.
├── docker-compose.yml        inference-llamacpp (padrão) · inference-max (experimental) · searxng · hermes · api · playground
├── prompts/base-de-conhecimento/pratos-mais-vendidos/   Analytics: brasil.md · <região>/_regiao.md · <região>/<estado>.md (zona, cidade, estado) · README

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
│       ├── plugin.yaml       manifesto (18 tools, 3 hooks, 1 web provider, zero dependências)
│       ├── __init__.py       register(ctx): tools, hooks e provider
│       ├── tools.py          handlers, travas (veredito único, evidência do perfil) + schemas (OpenAI function format)
│       ├── store.py          SQLite (despensa, perfil, receitas, pratos, compras, reservas, carrinho com pratos, salvos, negócio)
│       ├── falas.py          o que a Dona Maria disse (hooks) e a conferência de evidência
│       ├── painel.py         carrinho, saldo, salvos, perfil e mais vendidos injetados em todo turno
│       ├── negocio.py        perfil do negócio (opções fechadas) e o lugar da loja
│       ├── analytics.py      leitor da base de pratos mais vendidos e prioridade por zona, cidade, estado, região e país
│       ├── api.py            API HTTP da tela (carrinho, pratos, salvos, perfil do negócio, Analytics)
│       ├── xlsx_reader.py    leitor .xlsx em stdlib
│       ├── units.py          unidades da planilha + medidas caseiras + densidades
│       ├── matching.py       sinônimos + casamento aproximado
│       ├── costing.py        CMV, preço mínimo, cenários, simulação
│       ├── extract.py        provider receitas-local (urllib + JSON-LD Recipe + texto)
│       └── tests/            132 testes (unittest)
├── searxng/settings.yml      JSON ligado, pt-BR, engines barulhentos removidos
├── inference/llamacpp/README.md · inference/max/README.md
├── playground/               React (Vite) — chat no estilo do iFood, carrinho com pratos abertos e salvos, perfil do negócio, Analytics, roteiro da demo
│   ├── Dockerfile            build estático + nginx com proxy /hermes → :8642 e /api → :8700
│   └── src/                  sessão via X-Hermes-Session-Id e progresso de tools
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

O toolset é enxuto de propósito: ~26 tools (≈31 KB de schema) em vez das ~40 do bundle `hermes-cli`. Um modelo de 8B escolhe melhor com menos opções, e a consultora não precisa (nem deve ter) terminal, arquivos ou browser. O `tool_search` (meta-tool que esconde as tools de plugin até o modelo procurá-las) fica desligado: com ele ligado o Qwen3-8B não via as tools da despensa e respondia perguntas de custo com `web_search`. Sem `keyless_fallback`, nenhuma requisição sai para SaaS mesmo se um backend falhar.

## Modelo e desempenho medidos

| Item | Valor (RTX 5070 Ti 16 GB, driver 595) |
|---|---|
| Modelo | `unsloth/Qwen3-8B-GGUF` Q4_K_M, modo não-pensante |
| Contexto | 65 536 tokens (YaRN ×2 sobre 32 768), KV cache K q8_0 / V q4_0 |
| VRAM | ≈12,5 GB em uso no total com o desktop (≈9 GB do servidor) |
| Velocidade | ≈135 tokens/s de geração, ≈3 000 tokens/s de prompt |
| Teto de resposta | 4 096 tokens por geração (`LLAMA_N_PREDICT` no `.env`; `-1` desliga) |
| Latência | pergunta com 1 tool: 1–3 s · busca + extração + conferência de ingredientes (6 tool calls): ≈16 s · busca + extração de 2 páginas com resumo: ≈25 s |

Para mais qualidade com a GPU livre: `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` no `.env` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`). Para raciocínio explícito do Qwen3: `LLAMA_REASONING_BUDGET=-1`.

### Modular MAX (perfil `max`, experimental)

`make up-max` sobe `modular/max-nvidia-full:26.7.0` com `Qwen/Qwen3.5-4B` no lugar do llama.cpp. Em novembro de 2026 o `max serve` em Blackwell de consumo (sm_120) ainda está em validação pela Modular, só o Qwen3.5 tem parser de tool calling e não há streaming com function calling; por isso é perfil opcional, não o padrão. No spike feito nesta máquina o servidor subiu e planejou a memória da GPU, mas o worker foi morto pelo OOM killer do host durante a compilação do modelo (>16 GB de RAM); o relato completo e como repetir estão em [`inference/max/README.md`](inference/max/README.md).

## Testes e validação

```bash
make test        # 132 testes: leitor xlsx, unidades/conversões, casamento, SQLite, CMV/preço, fluxo das tools, travas (perfil pendente, forno ausente, orçamento estourado, números mudados), reservas/retry/remoção, extrator, carrinho e saldo, prato aberto (valor por ingrediente, "já tem" sem gastar saldo, prato como item único recusado), pratos e ingredientes salvos e migração do banco, Analytics (leitor, prioridade por lugar, a base do repositório sempre no formato), API da tela (inclusive HTTP de verdade), perfil do negócio com localização, painel e trava de evidência (com o incidente real de técnicas inventadas)
make validate    # hermes plugins validate (manifesto, registros, colisões, security scan) + toolsets + prompt-size
make smoke       # /health, tool_calls nativos, SearXNG JSON, plugin habilitado, resposta do agente com a despensa
```

## Limitações conhecidas

- Um modelo de 8B às vezes afirma o que a Dona Maria "já tem" antes de conferir; as travas das tools impedem que isso vire CMV, preço ou prato no cardápio, mas o texto da conversa ainda pode adiantar uma conclusão. Na demo vale pedir "confere na despensa".
- O código não distingue compra real de compra inventada. Numa prova com o modelo real, mandado "registrar sem perguntar", o Qwen3-8B tentou destravar pendências de preço lançando compras fictícias com `despensa_atualizar`; a trava segurou (nada entrou no cardápio) e `AGENTS.md` e a skill agora proíbem usar a tool para isso, mas é regra de prompt, não de backend.
- Conversões de medidas caseiras são estimativas (densidade, peso de "1 cebola"); a tool marca cada uma e o agente deve repassar as que pesam no custo.
- O Qwen3-8B às vezes não para de gerar; numa GPU dividida com o desktop isso já derrubou o llama.cpp (`CUDA error: the launch timed out`, o Docker o subiu de novo). O teto de 4 096 tokens por resposta reduz o risco, mas não vale para um cliente que peça `max_tokens` maior (o Hermes não pede). Se a resposta de uma conversa parar no meio, é o teto: suba `LLAMA_N_PREDICT`.
- O SearXNG depende de buscadores públicos; se um engine bloquear, os outros cobrem. Sem internet não há pesquisa de receitas.
- A tool `clarify` é da TUI (`make chat`); no playground e no API server (8642) a elicitação acontece em texto, porque o toolset HTTP não inclui `clarify`.
- A trava de evidência confere se o item foi citado (e o número, quando há), não se o sim/não bate com a fala: o modelo ainda pode gravar `true` para o que ela negou. A resposta do `clarify` na TUI foi coberta por teste unitário com o formato real do Hermes e por leitura do código dele, mas não foi exercitada de ponta a ponta com a TUI.
- O Hermes roda revisões em segundo plano que reaproveitam a sessão e chamam as tools minutos depois (passam pela mesma trava). Por isso, depois de um `make reset-dados`, comece a demo numa **conversa nova**: uma conversa antiga pode regravar o perfil dela. A API do Hermes também deriva o id da sessão da primeira mensagem, então repetir exatamente a mesma frase de abertura cai na conversa anterior.
- O carrinho é rascunho de compra: o aceite de prato (`cardapio_registrar`) continua olhando só os compromissos, não o carrinho. Dinheiro segue em `float` com arredondamento a dois centavos.
- **Os pratos mais vendidos são dados de exemplo**, gerados para a demonstração (12 estados, 23 cidades, 45 zonas, 47 pratos): servem para mostrar a priorização por lugar e a leitura pela consultora, não como estatística. Para usar de verdade, troque as tabelas pelos números da operação (README da pasta); o formato é conferido pelos testes.
- `carrinho_adicionar_prato` é rascunho de compra: não passa pela trava de viabilidade (equipamentos, técnicas, perfil) que `carrinho_montar_da_receita`, `prato_calcular_cmv`, `preco_sugerir` e `cardapio_registrar` aplicam; "confirmar que ela consegue cozinhar antes de comprar" fica, para o carrinho aberto à mão, na regra do `SOUL.md`/`AGENTS.md` (na prova ao vivo a consultora completou o perfil da cozinha antes de abrir o prato), e o aceite no cardápio continua com todas as travas.
- `carrinho_adicionar_prato` aceita os valores que a consultora passa (da despensa, de pesquisa ou ditos por ela, marcados como estimados quando for o caso). Um modelo de 8B às vezes erra uma conta no texto da conversa (já o vi somar errado um "a comprar"); por isso o carrinho, o total do prato e o saldo vêm da tool, e a consultora é instruída a copiar o `diga_a_dona_maria`.

## Próximos passos

- Spike do perfil MAX na RTX 50 e comparação com o llama.cpp no mesmo roteiro.
- Bônus em Mojo: matcher de ingredientes exposto ao Python via `PythonModuleBuilder`.
