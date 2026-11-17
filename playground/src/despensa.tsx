import { useEffect, useState } from "react";
import { KitchenIcon } from "./icons";
import { conferirDespensa, type ItemDespensa } from "./lib/api";
import { brl } from "./lib/format";
import { Sheet } from "./sheet";
import type { Estado } from "./types";

type Props = { onEstado: (estado: Estado) => void; onClose: () => void };

export function DespensaSheet({ onEstado, onClose }: Props) {
  const [itens, setItens] = useState<ItemDespensa[] | null>(null);
  const [falhou, setFalhou] = useState(false);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    let ativo = true;
    void conferirDespensa().then((resultado) => {
      if (!ativo) return;
      setItens(resultado.itens);
      onEstado(resultado.estado);
    }, () => {
      if (ativo) setFalhou(true);
    });
    return () => { ativo = false; };
  }, [onEstado, tentativa]);

  return (
    <Sheet titleId="despensa-titulo" title="Despensa" icon={<KitchenIcon />} onClose={onClose}>
      {falhou ? (
        <div>
          <p role="alert">A despensa está indisponível no momento.</p>
          <button type="button" className="link-btn" onClick={() => { setFalhou(false); setTentativa((valor) => valor + 1); }}>Tentar de novo</button>
        </div>
      ) : itens === null ? (
        <p className="sheet-note" role="status">Conferindo a despensa…</p>
      ) : itens.length === 0 ? (
        <p className="sheet-note">A despensa está vazia.</p>
      ) : (
        <table className="pantry-table">
          <thead><tr><th scope="col">Ingrediente</th><th scope="col">Disponível</th><th scope="col">Preço pago</th></tr></thead>
          <tbody>
            {itens.map((item) => (
              <tr key={item.nome}>
                <th scope="row">{item.nome}</th><td>{item.disponivel}</td><td>{item.preco_pago === null ? "—" : brl(item.preco_pago)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Sheet>
  );
}
