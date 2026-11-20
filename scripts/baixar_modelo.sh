#!/usr/bin/env bash
set -euo pipefail

arquivo="models/$MODEL_FILE"
if [[ ! "$MODEL_FILE" =~ ^[[:alnum:]_.-]+\.gguf$ ]]; then
    # Um modelo personalizado já presente continua sendo aceito pelo Compose.
    [[ ! -s "$arquivo" ]] || exit 0
    printf 'Coloque o modelo personalizado em %s ou informe o nome do arquivo GGUF.\n' "$arquivo" >&2
    exit 1
fi

mkdir -p models
parcial="$arquivo.part"
exec 9>"$parcial.lock"
flock 9

revisao=main
sha=""
if [[ "$MODEL_REPO/$MODEL_FILE" == unsloth/Qwen3-8B-GGUF/Qwen3-8B-Q4_K_M.gguf ]]; then
    revisao=a6adef130ffb23ddaf1a62fec9dced968c9bc482
    sha=120307ba529eb2439d6c430d94104dabd578497bc7bfe7e322b5d9933b449bd4
    bytes=5027784512
fi

verificar() {
    [[ -z "$sha" ]] || printf '%s  %s\n' "$sha" "$1" | sha256sum -c -
}

if [[ -s "$arquivo" ]]; then
    if ! verificar "$arquivo"; then
        printf 'Mova %s para outro local e rode make install para baixar uma cópia válida.\n' "$arquivo" >&2
        exit 1
    fi
    printf 'Já existe: %s\n' "$arquivo"
    exit 0
fi

if [[ -z "$sha" || ! -f "$parcial" || $(wc -c < "$parcial") -ne "$bytes" ]]; then
    curl --location --fail --retry 5 --retry-delay 5 --connect-timeout 15 --continue-at - \
        --output "$parcial" "https://huggingface.co/$MODEL_REPO/resolve/$revisao/$MODEL_FILE"
fi
if ! verificar "$parcial"; then
    mv -f -- "$parcial" "$arquivo.invalido"
    printf 'O arquivo baixado não passou na validação. Rode make install para baixar novamente.\n' >&2
    exit 1
fi
mv -- "$parcial" "$arquivo"
