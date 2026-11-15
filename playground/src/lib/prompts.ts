export type Prompt = {
  kicker: string;
  title: string;
  text: string;
};

// Boas-vindas do chat: texto fixo da interface, não vai no histórico enviado ao modelo.
export const WELCOME =
  "Oi, Dona Maria! Eu sou a consultora de cardápio do **Sabor da Maria**.\n\nPesquiso receitas de verdade, vejo o que mais vende na sua região, confiro a despensa, ajudo a montar o carrinho dentro do seu saldo e mostro a conta aberta, prato por prato e ingrediente por ingrediente. O preço quem escolhe é você.";

export const PEDIR_SUGESTOES =
  "Olha o perfil do meu negócio e os pratos mais vendidos na minha zona e cidade. Me sugere pratos, abre os ingredientes com o valor de cada um e coloca no carrinho o que falta, dentro do meu saldo.";

// Prato escolhido no Analytics: a consultora abre os ingredientes e os valores antes de pôr no carrinho.
export const montarPrato = (prato: string) =>
  `Quero vender "${prato}" no meu delivery. Abre os ingredientes com o valor de cada um e coloca no carrinho, dentro do meu saldo.`;

export const STARTERS: Prompt[] = [
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

export const ROTEIRO: Prompt[] = [
  {
    kicker: "1",
    title: "Apresentar o pedido",
    text: "Oi, sou a Dona Maria. Quero montar o cardápio com o que tenho na despensa.",
  },
  {
    kicker: "2",
    title: "Dizer o que gosta",
    text: "Gosto de fazer frango e carne moída, para marmita.",
  },
  {
    kicker: "3",
    title: "Escolher uma receita",
    text: "Gostei da primeira. Vamos ver se eu consigo fazer.",
  },
  {
    kicker: "4",
    title: "Contar a cozinha",
    text: "Tenho fogão de 4 bocas, forno, panela de pressão e liquidificador. Não tenho air fryer. A geladeira é média, tenho até 2 horas por cozinhada, gás no botijão e a energia é estável.",
  },
  {
    kicker: "5",
    title: "Montar o carrinho",
    text: "Coloca no carrinho o que falta para essa receita e me diz quanto sobra do meu saldo.",
  },
  {
    kicker: "6",
    title: "Pedir a conta",
    text: "Pode fechar esse prato. Me mostra o CMV e os cenários de preço, que eu escolho.",
  },
  {
    kicker: "7",
    title: "Ver os mais vendidos",
    text: "Quais pratos mais vendem na minha zona e na minha cidade? Escolhe um e abre os ingredientes com o valor de cada um.",
  },
];
