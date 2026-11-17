import { useId } from "react";
import { CheckIcon, ChevronIcon } from "./icons";
import type { Estado } from "./types";

const ETAPAS = [
  { id: "negocio", titulo: "Negócio", acao: "Preencher negócio" },
  { id: "despensa", titulo: "Despensa", acao: "Conferir despensa" },
  { id: "receitas", titulo: "Receitas", acao: "Pesquisar receitas" },
  { id: "cozinha", titulo: "Cozinha", acao: "Preencher cozinha" },
  { id: "ingredientes", titulo: "Ingredientes", acao: "Ver pratos escolhidos" },
  { id: "carrinho", titulo: "Carrinho", acao: "Abrir carrinho" },
  { id: "preco", titulo: "Custos e preço", acao: "Ver Analytics" },
  { id: "cardapio", titulo: "Cardápio", acao: "Ver meus pratos" },
] as const;

export type EtapaDoPrato = typeof ETAPAS[number]["id"];

export function etapasConcluidas(estado: Estado | null): Record<EtapaDoPrato, boolean> {
  const pratos = estado?.pratos_escolhidos ?? [];
  const aceito = pratos.some((prato) => prato.status === "aceito");
  const analisados = pratos.filter((prato) => prato.status === "cmv_calculado" || prato.status === "aceito");
  return {
    negocio: estado?.cadastro?.negocio_pendente === false,
    despensa: estado?.despensa_consultada === true,
    receitas: pratos.length > 0,
    cozinha: estado?.cadastro?.cozinha_pendente === false,
    ingredientes: analisados.length > 0 || pratos.some((prato) => prato.veredito === "viavel"),
    carrinho: aceito || !!estado?.carrinho.length || pratos.some((prato) => prato.veredito === "viavel" && prato.compras_estimadas === 0),
    preco: analisados.length > 0,
    cardapio: aceito,
  };
}

export function proximaEtapa(concluidas: Record<EtapaDoPrato, boolean>) {
  return ETAPAS.find((etapa) => !concluidas[etapa.id])?.id ?? "cardapio";
}

type Props = {
  etapa: EtapaDoPrato;
  concluidas: Record<EtapaDoPrato, boolean>;
  onEtapa: (etapa: EtapaDoPrato) => void;
  onAcao: (etapa: EtapaDoPrato) => void;
};

export function CaminhoDoPrato({ etapa, concluidas, onEtapa, onAcao }: Props) {
  const tituloId = useId();
  const index = ETAPAS.findIndex((item) => item.id === etapa);
  const atual = ETAPAS[index];
  const anterior = ETAPAS[index - 1];
  const seguinte = ETAPAS[index + 1];
  const acao = concluidas[atual.id] && (atual.id === "negocio" || atual.id === "cozinha")
    ? `Revisar ${atual.titulo.toLocaleLowerCase("pt-BR")}`
    : atual.acao;
  return (
    <section className="dish-path" aria-labelledby={tituloId}>
      <div className="dish-path-head">
        <h2 id={tituloId}>Caminho do prato</h2>
        <span>{index + 1} de {ETAPAS.length}</span>
      </div>
      <div className="dish-path-body">
        <button type="button" className="dish-path-arrow" aria-label="Etapa anterior" disabled={!anterior} onClick={() => onEtapa(anterior.id)}><ChevronIcon direction="left" /></button>
        <div className="dish-path-current">
          <p aria-live="polite">
            <strong>{atual.titulo}</strong>
            {concluidas[atual.id] ? <span className="dish-path-check"><CheckIcon /><span className="sr-only">Concluída</span></span> : null}
          </p>
          <button type="button" className="link-btn" onClick={() => onAcao(atual.id)}>{acao}</button>
        </div>
        <button type="button" className="dish-path-arrow" aria-label="Próxima etapa" disabled={!seguinte} onClick={() => onEtapa(seguinte.id)}><ChevronIcon direction="right" /></button>
      </div>
      <nav className="dish-path-stops" aria-label="Etapas do prato">
        {ETAPAS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={concluidas[item.id] ? "done" : undefined}
            aria-current={item.id === etapa ? "step" : undefined}
            aria-label={`${item.titulo}${concluidas[item.id] ? ", concluída" : ""}`}
            title={item.titulo}
            onClick={() => onEtapa(item.id)}
          ><span /></button>
        ))}
      </nav>
      {seguinte ? <p className="dish-path-next">A seguir, {seguinte.titulo.toLocaleLowerCase("pt-BR")}</p> : null}
    </section>
  );
}
