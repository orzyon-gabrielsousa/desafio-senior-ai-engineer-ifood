export type Prompt = {
  kicker: string;
  title: string;
  text: string;
};

// Boas-vindas do chat: texto fixo da interface, não vai no histórico enviado ao modelo.
export const WELCOME =
  "Oi, Dona Maria! Vamos montar seu cardápio, uma etapa de cada vez. Seu orçamento inicial para complementar os ingredientes que já tem é de R$ 80,00.";

export const PEDIR_SUGESTOES =
  "Pesquise na internet receitas para o meu delivery que aproveitem a minha despensa e combinem com a região da loja. Mostre as fontes e o que eu precisaria comprar. Quero escolher um prato com você.";

// Pedidos que o plugin conduz como busca de sugestões (jornada.delegou_ajuda): só leitura, lista conferida e
// nenhuma ficha antes da escolha. No segundo, as receitas que a conversa já mostrou ficam fora do resultado.
export const PEDIR_OPCOES_COM_A_IA =
  "Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.";
export const PEDIR_OUTRAS_OPCOES =
  "Quero sua ajuda. Pesquise outras receitas reais para o meu negócio com o estoque disponível, diferentes das que você já mostrou. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.";
// A frase que jornada.acao_da_fala reconhece: a consultora pergunta o prato ou o ingrediente sem passar pelo modelo.
export const PEDIR_PRATO_PROPRIO = "Quero montar meu próprio prato. Pergunte o que tenho em mente.";

export const PEDIR_ANALISE_DIVULGACAO = "Prepare a divulgação do meu cardápio.";
export const ACEITAR_ANALISE_DIVULGACAO = "Quero que você prepare a divulgação do meu cardápio.";

export const STARTERS: Prompt[] = [];
