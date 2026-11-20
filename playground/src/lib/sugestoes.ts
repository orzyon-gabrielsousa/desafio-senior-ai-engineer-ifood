import type { ChatMessage } from "../types";

/** Uma receita que a consultora mostrou como opção, com o número que ela tem na lista do chat. */
export type OpcaoSugerida = { numero: number; nome: string };

// A mesma leitura de escolhas.opcoes_exibidas no plugin, que é quem resolve a escolha: só vira opção a seção
// numerada que traz a fonte e fala dos ingredientes. Um passo a passo numerado não é uma lista de pratos.
const SECAO = /^\s*(?:#{1,6}\s+)?(?:✅\s*)?(?:\*\*)?(\d{1,2})[.)]\s+(.+?)\s*$/gm;
const FONTE = /^\s*(?:[-*]\s+)?\**fonte\s*:\**\s*(.+)$/im;
const INGREDIENTES = /\b(?:despensa|ja tem|precisa comprar|precisa conferir|ingredientes confirmados|faltando)\b/;
const LINK = /\[([^\]]+)\]\(https?:\/\/[^)]+\)/g;

// O plugin acha a lista até três falas atrás; a tela oferece os botões por menos tempo para nunca prometer
// uma escolha que a consultora já não consiga ligar à lista.
const FALAS_DEPOIS_DA_LISTA = 2;

function normalizar(texto: string): string {
  return texto.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/-/g, " ")
    .replace(/[^a-z0-9,./ ]+/g, " ").replace(/\s+/g, " ").trim();
}

export function opcoesExibidas(resposta: string): OpcaoSugerida[] {
  const secoes = [...resposta.matchAll(SECAO)];
  const opcoes: OpcaoSugerida[] = [];
  secoes.forEach((secao, indice) => {
    const inicio = secao.index + secao[0].length;
    const bloco = resposta.slice(inicio, indice + 1 < secoes.length ? secoes[indice + 1].index : resposta.length);
    if (!FONTE.test(bloco) || !INGREDIENTES.test(normalizar(bloco))) return;
    const nome = secao[2].replace(LINK, "$1").replace(/^[ *_`]+|[ *_`]+$/g, "");
    if (nome) opcoes.push({ numero: Number(secao[1]), nome });
  });
  return opcoes.every((opcao, indice) => opcao.numero === indice + 1) ? opcoes : [];
}

/**
 * As opções que ainda esperam a escolha dela nesta conversa: a lista mais recente, enquanto ela não a recusou
 * (nova busca ou prato próprio) nem a conversa seguiu para outro assunto.
 */
export function sugestoesPendentes(mensagens: ChatMessage[]): OpcaoSugerida[] {
  let falasDepois = 0;
  for (let indice = mensagens.length - 1; indice >= 0; indice -= 1) {
    const mensagem = mensagens[indice];
    if (mensagem.role === "user") {
      falasDepois += 1;
      if (mensagem.descartaSugestoes || falasDepois > FALAS_DEPOIS_DA_LISTA) return [];
    } else if (!mensagem.pending && !mensagem.error) {
      const opcoes = opcoesExibidas(mensagem.content);
      if (opcoes.length) return opcoes;
    }
  }
  return [];
}
