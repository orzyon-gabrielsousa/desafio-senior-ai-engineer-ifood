export type Prompt = {
  kicker: string;
  title: string;
  text: string;
};

// Boas-vindas do chat: texto fixo da interface, não vai no histórico enviado ao modelo.
export const WELCOME =
  "Oi, Dona Maria! Eu sou a consultora de cardápio do **Sabor da Maria**.\n\nPesquiso receitas de verdade, vejo o que mais vende na sua região, confiro a despensa, ajudo a montar o carrinho dentro do seu saldo e mostro a conta aberta, prato por prato e ingrediente por ingrediente. O preço quem escolhe é você.\n\nAntes de qualquer prato, vou conhecer a sua **cozinha**: equipamentos, utensílios, técnicas e a rotina. O que você contar fica salvo em **Minha cozinha** (menu), onde você também pode marcar e corrigir.";

export const PEDIR_SUGESTOES =
  "Olha o perfil do meu negócio e os pratos mais vendidos na minha zona e cidade. Me sugere pratos, abre os ingredientes com o valor de cada um e coloca no carrinho o que falta, dentro do meu saldo.";

// Prato escolhido no Analytics: a consultora abre os ingredientes e os valores antes de pôr no carrinho.
export const montarPrato = (prato: string) =>
  `Quero vender "${prato}" no meu delivery. Abre os ingredientes com o valor de cada um e coloca no carrinho, dentro do meu saldo.`;

export const STARTERS: Prompt[] = [
  {
    kicker: "Cozinha",
    title: "Contar da minha cozinha",
    text: "Oi, sou a Dona Maria. Quero contar como é a minha cozinha.",
  },
  {
    kicker: "Começar",
    title: "Abrir a despensa",
    text: "Oi, sou a Dona Maria. Quero montar o cardápio com o que tenho na despensa.",
  },
  {
    kicker: "Estilo",
    title: "Marmita de casa",
    text: "Gosto de fazer frango e carne moída, para marmita.",
  },
  {
    kicker: "Números",
    title: "Quanto eu paguei",
    text: "O que eu tenho na despensa e quanto paguei em cada coisa?",
  },
  {
    kicker: "Negócio",
    title: "Sugestões do meu negócio",
    text: PEDIR_SUGESTOES,
  },
  {
    kicker: "Fechamento",
    title: "Cardápio e saldo",
    text: "Mostra o cardápio e quanto ainda tenho dos R$ 80.",
  },
];
