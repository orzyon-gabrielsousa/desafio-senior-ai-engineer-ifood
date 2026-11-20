import { CheckIcon, CloseIcon } from "./icons";
import { chave } from "./lib/format";
import type { IngredienteDaFicha } from "./types";

const STATUS = {
  disponivel: "Disponível",
  faltando: "Falta no estoque",
  a_conferir: "A conferir",
} as const;

export function IngredientesFicha({ ingredientes = [], aberto = false }: { ingredientes?: IngredienteDaFicha[]; aberto?: boolean }) {
  if (!ingredientes.length) return null;
  const faltantes = ingredientes.filter(({ estado }) => estado === "faltando").length;
  return <details className="ingredientes-ficha" open={aberto}>
    <summary>Conferência do estoque {faltantes ? <small>{faltantes} em falta</small> : null}</summary>
    <ul className="ingredientes-ficha-lista">
      {ingredientes.map((linha, indice) => {
        const { ingrediente, quantidade, estado, item_despensa, uso_despensa, falta, derivado, omitido, substitui } = linha;
        const insumo = item_despensa && chave(item_despensa) !== chave(ingrediente) ? item_despensa : null;
        return <li key={`${ingrediente}:${indice}`}>
          <div>
            <strong>{ingrediente}</strong>{quantidade ? <small> · {quantidade}</small> : null}
            {derivado && insumo ? <small className="ingrediente-insumo">{uso_despensa ? `Preparo com ${uso_despensa} de ${insumo}` : `Preparo com ${insumo}`}</small>
              : insumo ? <small className="ingrediente-insumo">Na despensa como {insumo}</small> : null}
            {substitui ? <small className="ingrediente-insumo">Substitui {substitui}</small> : null}
            {omitido ? <small className="ingrediente-insumo">Fora da versão atual</small> : null}
          </div>
          <span className={`ingrediente-estado ${estado}`}>
            <span aria-hidden="true">{estado === "disponivel" ? <CheckIcon /> : estado === "faltando" ? <CloseIcon /> : "?"}</span>
            {estado === "faltando" && falta ? `Falta ${falta}` : STATUS[estado]}
          </span>
        </li>;
      })}
    </ul>
  </details>;
}
