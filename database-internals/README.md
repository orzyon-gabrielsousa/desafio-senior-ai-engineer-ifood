# Memória local do Sabor da Maria

O playground guarda suas conversas em `conversas.sqlite3`, junto com o ID que permite retomar a sessão no Hermes. O Hermes mantém o histórico original em `hermes-home/state.db`. Ao abrir outro chat, a consultora pode recuperar trechos relevantes de sessões locais anteriores e dos arquivos que você adicionou a `contexto/`. O painel **Agente de IA** mostra a quantidade de conversas, arquivos e vetores disponíveis.

Coloque os textos que quiser compartilhar com a consultora diretamente em `contexto/`, em `.md` ou `.txt` UTF-8 (até 24 arquivos de 64 KiB cada). Os arquivos são lidos no próximo turno. Para usar conteúdo de um chat do Codex ou de outra aplicação, copie somente os trechos que quiser importar. As referências recuperadas servem como contexto. Ações sobre estoque, preços e cardápio exigem o pedido e as confirmações do turno atual.

O índice `memoria/chat.sqlite3` guarda trechos de texto e vetores de 1.024 dimensões (`float32`). `make modelo-embeddings` baixa e verifica o modelo local Qwen3-Embedding. Sem o modelo, a busca funciona por texto. O arquivo do modelo fica em `models/`.

No perfil padrão, o llama.cpp mantém o cache KV na VRAM e salva uma cópia compatível dos tensores do slot ativo em `kv/`. A cópia serve para reaproveitar tokens do mesmo prefixo após uma reinicialização. A recuperação de conversas usa o histórico e o índice de memória. Veja [a configuração do llama.cpp](../inference/llamacpp/README.md) para controlar a frequência das gravações.

Os arquivos de runtime ficam fora do Git. Excluir uma conversa no playground também a retira da recuperação de contexto. O Hermes ainda mantém sua transcrição original. `make reset-dados` arquiva o histórico, o índice e o cache KV em `.sabor-backups/`. Os arquivos de referência que você colocou em `contexto/` ficam disponíveis para a próxima execução.
