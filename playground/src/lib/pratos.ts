import type { PratoEscolhido } from "../types";

export type SituacaoPrato = {
  etapa: "rascunho" | "pendente" | "aceite" | "preco" | "aprovado" | "revisao";
  titulo: string;
  tom: "confirmar" | "atencao" | "nao_da" | "ok";
  descricao: string;
  acao: string;
  destino: "chat" | "analytics";
};

export function pendenciasDoPrato(prato: PratoEscolhido): string[] {
  const textos = prato.bloqueios?.length ? prato.bloqueios.map(({ detalhe }) => detalhe) : prato.pendencias ?? [];
  return [...new Set(textos.map((texto) => texto.trim()).filter(Boolean))];
}

/** Uma divergência da ficha é trabalho da consultora, não uma informação da cozinha. */
export function fichaPrecisaDaFonte(prato: PratoEscolhido): boolean {
  return prato.status !== "aceito" && Boolean(prato.bloqueios?.some(({ tipo }) => tipo === "fonte_incompleta" || tipo === "fonte_divergente"));
}

/** Retomar uma ficha não confirma requisitos operacionais nem aprova uma versão. */
export function mensagemParaContinuarPrato(prato: PratoEscolhido): { visivel: string; paraAgente: string } {
  if (fichaPrecisaDaFonte(prato)) return {
    visivel: `Complete a ficha de «${prato.nome}» conferindo a receita original, sem alterar os ingredientes.`,
    paraAgente: `Quero continuar o prato escolhido #${prato.id} (${prato.nome}). Complete a ficha a partir da receita original salva${prato.fonte_url ? ` em ${prato.fonte_url}` : ""}. Leia novamente a fonte e preencha ou valide todos os ingredientes, medidas, rendimento e preparo, sem omitir ou substituir itens. Se a fonte não trouxer um dado, explique só esse ponto. Não transforme esta retomada em aceite, compra, reserva ou confirmação da cozinha.`,
  };
  if (prato.status === "selecionado" && prato.bloqueios?.[0]?.tipo === "tempo_insuficiente") return {
    visivel: `O que podemos adaptar no preparo de «${prato.nome}» para caber no meu tempo?`,
    paraAgente: `Vamos continuar o prato #${prato.id}, ${prato.nome}. Resolva comigo as pendências dessa versão.`,
  };
  const etapa = situacaoDoPrato(prato).etapa;
  const visivel = etapa === "preco"
    ? `Mostre os cenários de preço de «${prato.nome}» aqui no chat para eu escolher.`
    : etapa === "aprovado"
    ? `Revise o preço e o lucro de «${prato.nome}» e me diga se precisamos ajustar algo.`
    : etapa === "revisao"
    ? `Quero revisar «${prato.nome}» antes de produzir nas condições atuais. Confira a pendência e me conduza pelo ajuste, preservando o registro e as reservas até decidirmos o que fazer.`
    : etapa === "pendente"
    ? `Retome a próxima pergunta da ficha de «${prato.nome}» para eu responder.`
    : etapa === "aceite"
    ? `Quero revisar a versão de «${prato.nome}» para decidir se vou prepará-la.`
    : `Vamos montar a ficha de «${prato.nome}» com a minha despensa e a minha cozinha.`;
  return {
    visivel,
    paraAgente: `Quero continuar o prato escolhido #${prato.id} (${prato.nome}). ${visivel} Esta retomada não é uma resposta sobre a cozinha nem um aceite do prato. Use os dados salvos e pergunte somente o que falta.`,
  };
}

/** Ter custo calculado não representa aceite nem decisão de preço. */
export function situacaoDoPrato(prato: PratoEscolhido): SituacaoPrato {
  const temPendencias = pendenciasDoPrato(prato).length > 0 || prato.veredito === "precisa_confirmar" || prato.veredito === "inviavel_por_enquanto";
  if (prato.status === "aceito" && temPendencias) return {
    etapa: "revisao", titulo: "Precisa de revisão", tom: "atencao", destino: "chat", acao: "Revisar com a consultora",
    descricao: "Seu preço continua registrado, mas as condições atuais precisam de revisão antes de produzir. Vamos resolver a mudança com a consultora.",
  };
  if (prato.status === "aceito") return {
    etapa: "aprovado", titulo: "Aprovado no cardápio", tom: "ok", destino: "analytics", acao: "Ver preço e resultado",
    descricao: "Seu preço está registrado. Consulte os custos e o resultado por porção.",
  };
  if (fichaPrecisaDaFonte(prato)) return {
    etapa: "pendente", titulo: "Ficha a conferir", tom: "atencao", destino: "chat", acao: "Completar ficha com a consultora",
    descricao: "Sua escolha está salva. A consultora pode revisar a ficha com a receita original e o estoque disponível.",
  };
  if (temPendencias && prato.ingredientes_faltantes?.some(({ estado }) => estado === "faltando")) return {
    etapa: "pendente", titulo: "Salvo com faltantes", tom: "atencao", destino: "chat", acao: "Continuar a montagem",
    descricao: "O prato está salvo. Podemos pesquisar o preço do que falta ou adaptar o preparo ao seu estoque.",
  };
  if (temPendencias) return {
    etapa: "pendente", titulo: "Pendências a resolver", tom: prato.veredito === "inviavel_por_enquanto" ? "nao_da" : "atencao",
    destino: "chat", acao: prato.bloqueios?.[0]?.tipo === "tempo_insuficiente" ? "Resolver tempo de preparo" : "Responder no chat",
    descricao: "A ficha está salva. Vamos conferir o que falta para preparar esta versão.",
  };
  if (prato.analise_preco_salva) return {
    etapa: "preco", titulo: "Aguardando seu preço", tom: "atencao", destino: "analytics", acao: "Comparar e escolher preço",
    descricao: "A versão foi aceita e os cenários estão prontos. Falta você escolher quanto cobrar.",
  };
  if (prato.veredito === "viavel" || prato.status === "cmv_calculado") return {
    etapa: "aceite", titulo: "Aguardando seu aceite", tom: "atencao", destino: "chat", acao: "Revisar a versão no chat",
    descricao: "Revise os ingredientes, o rendimento e o preparo para decidir se quer preparar esta versão.",
  };
  return {
    etapa: "rascunho", titulo: "Rascunho", tom: "confirmar", destino: "chat", acao: "Montar a ficha no chat",
    descricao: "Sua escolha está guardada. Falta conferir a receita com a despensa e a cozinha.",
  };
}
