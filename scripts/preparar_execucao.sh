#!/usr/bin/env bash
set -euo pipefail

acao=$1
comando_make=$2
shift 2
compose=("$@")

falhar() {
    printf '%s\n' "$1" >&2
    exit 1
}

"${compose[@]}" version >/dev/null 2>&1 || falhar 'Instale o Docker com o plugin Docker Compose. Veja a seção Rodando no README.'
"${compose[@]}" ps -q >/dev/null || falhar 'Verifique se o Docker está em execução e se o seu usuário consegue acessá-lo.'

servicos=$("${compose[@]}" config --services)
motor=""
while IFS= read -r servico; do
    case "$servico" in
        inference-llamacpp|inference-max)
            [[ -z "$motor" ]] || falhar 'Use somente um perfil de inferência por vez no COMPOSE_PROFILES.'
            motor=$servico
            ;;
    esac
done <<< "$servicos"
[[ -n "$motor" ]] || falhar 'Defina COMPOSE_PROFILES=llamacpp no .env para ativar o motor padrão.'

if [[ "$motor" == inference-llamacpp ]]; then
    modelo=$("${compose[@]}" config --environment | sed -n 's/^LLAMA_MODEL_FILE=//p') || \
        falhar 'Confira a configuração e use Docker Compose v2.28 ou posterior.'
    modelo=${modelo:-Qwen3-8B-Q4_K_M.gguf}
    if [[ "$acao" == conferir && ! -s "models/$modelo" ]]; then
        falhar "Falta o modelo models/$modelo. Execute make install antes de subir o projeto."
    fi
fi

[[ "$acao" == instalar ]] || exit 0
for utilitario in curl sha256sum flock; do
    command -v "$utilitario" >/dev/null || falhar "Instale $utilitario no host e rode make install novamente."
done

printf '\n==> Preparando imagens do Docker\n'
"${compose[@]}" pull --ignore-buildable --policy missing
printf '\n==> Verificando acesso à GPU NVIDIA pelo Docker\n'
"${compose[@]}" run --rm --no-deps -T --entrypoint nvidia-smi "$motor" -L || \
    falhar 'O Docker precisa acessar a GPU NVIDIA. Confira o driver e o NVIDIA Container Toolkit na seção Rodando do README.'

printf '\n==> Preparando modelos locais\n'
if [[ "$motor" == inference-llamacpp ]]; then
    "$comando_make" --no-print-directory model "MODEL_FILE=$modelo"
fi
"$comando_make" --no-print-directory modelo-embeddings

printf '\n==> Compilando o playground\n'
"${compose[@]}" build playground
printf '\n==> Inicializando o SQLite e a despensa\n'
"${compose[@]}" run --rm --no-deps -T --entrypoint /opt/hermes/.venv/bin/python3 api -c \
    'from sabor_da_maria.store import Store; Store().ensure_loaded()'
printf '\nInstalação concluída. Execute make run e abra http://localhost:3000 no ambiente padrão.\n'
