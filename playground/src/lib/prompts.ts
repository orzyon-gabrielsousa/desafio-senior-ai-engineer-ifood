export type Prompt = {
  kicker: string;
  title: string;
  text: string;
};

// Boas-vindas do chat: texto fixo da interface, não vai no histórico enviado ao modelo.
export const WELCOME =
  "Oi, Dona Maria! Sou sua consultora de cardápio. Vamos conhecer seu negócio e sua cozinha para montar pratos que você consegue preparar e vender.";

export const PEDIR_SUGESTOES =
  "Pesquise na internet receitas para o meu delivery que aproveitem a minha despensa e combinem com a região da loja. Mostre as fontes e o que eu precisaria comprar. Quero escolher um prato com você.";

export const STARTERS: Prompt[] = [
  {
    kicker: "Começar",
    title: "Contar do meu negócio",
    text: "Oi, sou a Dona Maria. Quero contar do meu negócio e da minha cozinha.",
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
    kicker: "Fechamento",
    title: "Cardápio e saldo",
    text: "Mostra o cardápio e quanto ainda tenho dos R$ 80.",
  },
];
