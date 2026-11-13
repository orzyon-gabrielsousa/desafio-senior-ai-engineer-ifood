# Sabor da Maria — atalhos. `make help` lista tudo.
SHELL := /bin/bash
COMPOSE ?= docker compose
INFERENCE_HOST_PORT ?= 8010
MODEL_REPO ?= unsloth/Qwen3-8B-GGUF
MODEL_FILE ?= Qwen3-8B-Q4_K_M.gguf
Q ?= Liste três itens da despensa com o custo unitário de cada um.

.PHONY: help setup model up down restart logs status chat ask test validate smoke reset-dados shell up-max down-max

help: ## mostra esta ajuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## cria .env a partir do exemplo e as pastas de runtime
	@test -f .env || cp .env.example .env
	@mkdir -p models hermes-home/sabor_da_maria
	@sed -i "s/^HERMES_UID=.*/HERMES_UID=$$(id -u)/; s/^HERMES_GID=.*/HERMES_GID=$$(id -g)/" .env
	@echo "ok: .env pronto (HERMES_UID=$$(id -u))"

model: ## baixa o GGUF do Qwen3-8B (MODEL_REPO/MODEL_FILE) para ./models
	@mkdir -p models
	@test -f models/$(MODEL_FILE) && echo "já existe: models/$(MODEL_FILE)" || \
	 (curl -L --fail --retry 5 --retry-delay 5 -C - -o models/$(MODEL_FILE).part \
	   "https://huggingface.co/$(MODEL_REPO)/resolve/main/$(MODEL_FILE)" && mv models/$(MODEL_FILE).part models/$(MODEL_FILE))

up: ## sobe inferência (perfil do .env), SearXNG e Hermes
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory status

down: ## derruba tudo
	$(COMPOSE) --profile llamacpp --profile max down

restart: ## reinicia só o Hermes (depois de editar config/SOUL/plugin)
	$(COMPOSE) restart hermes

logs: ## logs de todos os serviços
	$(COMPOSE) logs -f --tail=100

status: ## saúde dos serviços
	@$(COMPOSE) ps
	@echo "--- inference"; curl -fsS http://localhost:$(INFERENCE_HOST_PORT)/health && echo || echo "(ainda não respondeu)"
	@echo "--- searxng";   curl -fsS "http://localhost:8888/search?q=teste&format=json" -o /dev/null -w "HTTP %{http_code}\n" || echo "(ainda não respondeu)"
	@echo "--- hermes api"; curl -fsS http://localhost:8642/health && echo || echo "(API server ainda não respondeu)"

chat: ## abre a TUI do Hermes com o playbook pré-carregado (a demo)
	$(COMPOSE) exec -it hermes hermes chat -s consultoria-cardapio

ask: ## pergunta única sem TUI: make ask Q="..."
	$(COMPOSE) exec -T hermes hermes chat -q "$(Q)"

test: ## testes unitários do plugin (só stdlib, roda no host)
	python3 -m unittest discover -s hermes-home/plugins/sabor_da_maria/tests -t hermes-home/plugins -v

validate: ## valida o plugin com o próprio Hermes e lista toolsets/skills ativos
	$(COMPOSE) exec -T hermes hermes plugins validate /opt/data/plugins/sabor_da_maria
	$(COMPOSE) exec -T hermes hermes plugins list --plain --no-bundled
	$(COMPOSE) exec -T hermes hermes tools list
	$(COMPOSE) exec -T hermes hermes prompt-size

smoke: ## smoke test ponta a ponta (inferência, busca, tools, Hermes)
	./scripts/smoke_test.sh

reset-dados: ## apaga o SQLite do plugin (perfil, receitas, cardápio); a planilha é recarregada
	rm -f hermes-home/sabor_da_maria/sabor_da_maria.db*
	@echo "ok: dados da consultoria zerados"

shell: ## shell dentro do container do Hermes
	$(COMPOSE) exec -it hermes bash

up-max: ## sobe o perfil experimental MAX no lugar do llama.cpp
	$(COMPOSE) --profile llamacpp stop inference-llamacpp || true
	$(COMPOSE) --profile max up -d inference-max

down-max: ## volta para o llama.cpp
	$(COMPOSE) --profile max stop inference-max || true
	$(COMPOSE) --profile llamacpp up -d inference-llamacpp
