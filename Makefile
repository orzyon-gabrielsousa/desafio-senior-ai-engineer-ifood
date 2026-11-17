# Sabor da Maria — atalhos. `make help` lista tudo.
SHELL := /bin/bash
COMPOSE ?= docker compose
INFERENCE_HOST_PORT ?= 8010
PLAYGROUND_HOST_PORT ?= 3000
MODEL_REPO ?= unsloth/Qwen3-8B-GGUF
MODEL_FILE ?= Qwen3-8B-Q4_K_M.gguf
Q ?= Liste três itens da despensa com o custo unitário de cada um.

.PHONY: help setup prepare-runtime model modelo-embeddings run up down restart logs status chat ask test validate smoke reset-dados shell up-max down-max playground playground-dev

help: ## mostra esta ajuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## cria .env a partir do exemplo e as pastas de runtime
	@test -f .env || cp .env.example .env
	@mkdir -p models hermes-home/sabor_da_maria
	@sed -i "s/^HERMES_UID=.*/HERMES_UID=$$(id -u)/; s/^HERMES_GID=.*/HERMES_GID=$$(id -g)/" .env
	@echo "ok: .env pronto (HERMES_UID=$$(id -u))"

prepare-runtime:
	@test -f .env || $(MAKE) --no-print-directory setup
	@mkdir -p hermes-home/sabor_da_maria
	@install -d -m 700 database-internals database-internals/contexto database-internals/kv

model: ## baixa o GGUF do Qwen3-8B (MODEL_REPO/MODEL_FILE) para ./models
	@mkdir -p models
	@test -f models/$(MODEL_FILE) && echo "já existe: models/$(MODEL_FILE)" || \
	 (curl -L --fail --retry 5 --retry-delay 5 -C - -o models/$(MODEL_FILE).part \
	   "https://huggingface.co/$(MODEL_REPO)/resolve/main/$(MODEL_FILE)" && mv models/$(MODEL_FILE).part models/$(MODEL_FILE))

modelo-embeddings: ## baixa e verifica o Qwen3-Embedding 0.6B para busca semântica local
	@mkdir -p models
	@set -e; \
	 modelo=models/Qwen3-Embedding-0.6B-Q8_0.gguf; \
	 sha=06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439; \
	 if [ -f "$$modelo" ]; then printf '%s  %s\n' "$$sha" "$$modelo" | sha256sum -c -; exit; fi; \
	 parcial=$$(mktemp "$$modelo.part.XXXXXX"); \
	 trap 'rm -f -- "$$parcial"' EXIT; \
	 curl -fL --retry 3 --connect-timeout 15 -o "$$parcial" \
	   'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/370f27d7550e0def9b39c1f16d3fbaa13aa67728/Qwen3-Embedding-0.6B-Q8_0.gguf'; \
	 printf '%s  %s\n' "$$sha" "$$parcial" | sha256sum -c -; \
	 chmod 0644 "$$parcial"; \
	 mv -- "$$parcial" "$$modelo"

run: prepare-runtime ## sobe tudo em primeiro plano, com os logs na tela; Ctrl+C derruba tudo
	@echo "==> subindo a stack; o playground aguarda os serviços e o SQLite. Ctrl+C derruba tudo."
	@parar() { \
	   if [ -n "$$($(COMPOSE) --profile llamacpp --profile max ps -q)" ]; then \
	     $(COMPOSE) --profile llamacpp --profile max stop; \
	   fi; \
	   echo "==> stack parada"; \
	 }; \
	 trap parar EXIT; trap 'exit 0' INT TERM HUP; \
	 $(COMPOSE) up --build --force-recreate --menu=false

up: prepare-runtime ## sobe tudo em segundo plano, para scripts (use make down para parar)
	$(COMPOSE) up --build --wait
	@$(MAKE) --no-print-directory status

down: ## derruba tudo
	$(COMPOSE) --profile llamacpp --profile max down

restart: ## reinicia o Hermes e a API do carrinho (depois de editar config/SOUL/plugin)
	$(COMPOSE) restart hermes api

logs: ## logs de todos os serviços
	$(COMPOSE) logs -f --tail=100

status: ## saúde dos serviços
	@$(COMPOSE) ps
	@echo "--- inference"; curl -fsS http://localhost:$(INFERENCE_HOST_PORT)/health && echo || echo "(ainda não respondeu)"
	@echo "--- searxng";   curl -fsS "http://localhost:8888/search?q=teste&format=json" -o /dev/null -w "HTTP %{http_code}\n" || echo "(ainda não respondeu)"
	@echo "--- hermes api"; curl -fsS http://localhost:8642/health && echo || echo "(API server ainda não respondeu)"
	@echo "--- api (carrinho, via nginx)"; curl -fsS http://localhost:$(PLAYGROUND_HOST_PORT)/api/saude && echo || echo "(ainda não respondeu)"
	@echo "--- playground  http://localhost:$(PLAYGROUND_HOST_PORT)"; curl -fsS -o /dev/null -w "HTTP %{http_code}\n" http://localhost:$(PLAYGROUND_HOST_PORT)/ || echo "(ainda não respondeu)"

chat: ## abre a TUI do Hermes com o playbook pré-carregado (a demo)
	$(COMPOSE) exec -it hermes hermes chat -s consultoria-cardapio

ask: ## pergunta única sem TUI: make ask Q="..."
	$(COMPOSE) exec -T hermes hermes chat -q "$(Q)"

test: ## testes unitários do plugin e do reset (só stdlib, roda no host)
	python3 -m unittest discover -s hermes-home/plugins/sabor_da_maria/tests -t hermes-home/plugins -v
	python3 scripts/test_reset_dados.py -v

validate: ## valida o plugin com o próprio Hermes e lista toolsets/skills ativos
	$(COMPOSE) exec -T hermes hermes plugins validate /opt/data/plugins/sabor_da_maria
	$(COMPOSE) exec -T hermes hermes plugins list --plain --no-bundled
	$(COMPOSE) exec -T hermes hermes tools list
	$(COMPOSE) exec -T hermes hermes prompt-size

smoke: ## smoke test ponta a ponta (inferência, busca, tools, Hermes)
	./scripts/smoke_test.sh

reset-dados: ## para o app e zera os dados e conversas da demo, com cópia local
	$(COMPOSE) --profile llamacpp stop playground hermes api inference-llamacpp
	python3 scripts/reset_dados.py

shell: ## shell dentro do container do Hermes
	$(COMPOSE) exec -it hermes bash

up-max: ## sobe o perfil experimental MAX no lugar do llama.cpp
	$(COMPOSE) --profile llamacpp stop inference-llamacpp || true
	$(COMPOSE) --profile max up -d inference-max

down-max: ## volta para o llama.cpp
	$(COMPOSE) --profile max stop inference-max || true
	$(COMPOSE) --profile llamacpp up -d inference-llamacpp

playground: prepare-runtime ## reconstrói o playground e sobe suas dependências
	$(COMPOSE) up -d --build playground

playground-dev: ## playground com recarga rápida; precisa de Node 20+ e do Hermes no ar
	@test -d playground/node_modules || npm install --prefix playground
	npm run dev --prefix playground
