import { useMemo, useState } from "react";
import { CheckIcon, CloseIcon, KitchenIcon } from "./icons";
import { Sheet } from "./sheet";
import type { Acao, Cozinha, Estado, Nivel, NovaCozinha, OpcaoCozinha, OpcoesCozinha, Outro } from "./types";

// O que ela tem e sabe fazer, no mesmo perfil que a consultora lê e grava: o que ela conta na conversa aparece aqui,
// e o que ela marca aqui vale para a consultora, para o carrinho e para as travas do cardápio.

/** O formulário: equipamentos e utensílios sim/não, o fogão com as bocas, habilidades em três níveis (ou sem resposta) e a rotina. */
type Form = {
  tem: Record<string, boolean>;
  bocas: number | null;
  habilidades: Record<string, Nivel | null>;
  rotina: Record<string, string>; // "" = não respondi
};

const NIVEIS: { id: Nivel; curto: string }[] = [
  { id: "sim", curto: "Sei" },
  { id: "aprendendo", curto: "Aprendendo" },
  { id: "nao", curto: "Não sei" },
];

const ROTULO_DO_GRUPO: Record<Outro["grupo"], string> = {
  equipamentos: "Equipamento ou utensílio",
  tecnicas: "Habilidade",
  restricoes: "Rotina",
  observacoes: "Observação",
};

function doEstado(cozinha: Cozinha, opcoes: OpcoesCozinha): Form {
  const fogao = cozinha.equipamentos["fogao"];
  return {
    tem: Object.fromEntries([...opcoes.equipamentos, ...opcoes.utensilios].map((o) => [o.id, Boolean(cozinha.equipamentos[o.id])])),
    bocas: typeof fogao === "number" ? fogao : null,
    habilidades: Object.fromEntries(opcoes.habilidades.map((o) => [o.id, cozinha.habilidades[o.id] ?? null])),
    rotina: Object.fromEntries(opcoes.rotina.map((r) => [r.id, r.id in cozinha.rotina ? String(cozinha.rotina[r.id]) : ""])),
  };
}

/** Só o que mudou nas habilidades e na rotina (um valor que veio da conversa e não está nas opções volta intacto, sem ser reenviado). */
function paraEnvio(form: Form, inicial: Form, opcoes: OpcoesCozinha): NovaCozinha {
  const equipamentos: Record<string, boolean | number> = {};
  for (const [id, tem] of Object.entries(form.tem)) equipamentos[id] = id === "fogao" && tem && form.bocas ? form.bocas : tem;
  const habilidades: Record<string, Nivel | null> = {};
  for (const [id, nivel] of Object.entries(form.habilidades)) if (nivel !== inicial.habilidades[id]) habilidades[id] = nivel;
  const rotina: Record<string, string | number | null> = {};
  for (const r of opcoes.rotina) {
    const bruto = form.rotina[r.id];
    if (bruto === inicial.rotina[r.id]) continue;
    rotina[r.id] = bruto === "" ? null : (r.opcoes.find((o) => String(o.valor) === bruto)?.valor ?? bruto);
  }
  return { equipamentos, habilidades, rotina };
}

function Chips({ itens, form, onToggle }: { itens: OpcaoCozinha[]; form: Form; onToggle: (id: string) => void }) {
  return (
    <div className="toggle-chips" role="group">
      {itens.map((item) => {
        const tem = form.tem[item.id];
        return (
          <button
            key={item.id}
            type="button"
            className={tem ? "toggle on" : "toggle"}
            aria-pressed={tem}
            aria-label={item.label}
            title={item.dica || undefined}
            onClick={() => onToggle(item.id)}
          >
            {tem ? <CheckIcon /> : null}
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

type KitchenSheetProps = {
  estado: Estado | null;
  onClose: () => void;
  onSave: (dados: NovaCozinha) => Acao;
};

export function KitchenSheet({ estado, onClose, onSave }: KitchenSheetProps) {
  const cozinha = estado?.cozinha ?? null;
  const opcoes = estado?.opcoes.cozinha ?? null;
  // O formulário é derivado do que está salvo: enquanto ela não mexe, acompanha o que a conversa gravar; mexeu, vale o que ela editou.
  const inicial = useMemo(() => (cozinha && opcoes ? doEstado(cozinha, opcoes) : null), [cozinha, opcoes]);
  const [editado, setEditado] = useState<Form | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  if (!cozinha || !opcoes || !inicial) {
    return (
      <Sheet titleId="cozinha-titulo" title="Minha cozinha" icon={<KitchenIcon />} onClose={onClose} wide>
        <p className="sheet-note error">Não consegui ler a cozinha. Confere se a API subiu com o make up.</p>
      </Sheet>
    );
  }

  const form = editado ?? inicial;
  const sujo = editado !== null && JSON.stringify(editado) !== JSON.stringify(inicial);
  const { respondidos, total } = cozinha.resumo;
  const mudar = (parte: Partial<Form>) => setEditado({ ...form, ...parte });
  const alternar = (id: string) => mudar({ tem: { ...form.tem, [id]: !form.tem[id] }, ...(id === "fogao" && form.tem.fogao ? { bocas: null } : {}) });
  const escolherNivel = (id: string, nivel: Nivel | null) => mudar({ habilidades: { ...form.habilidades, [id]: nivel } });

  async function salvar() {
    if (!opcoes || !inicial) return;
    setOcupado(true);
    const falha = await onSave(paraEnvio(form, inicial, opcoes));
    setOcupado(false);
    setErro(falha);
    if (falha === null) onClose();
  }

  async function apagar(outro: Outro) {
    setOcupado(true);
    setErro(await onSave({ remover: [{ grupo: outro.grupo, chave: outro.chave }] }));
    setOcupado(false);
  }

  return (
    <Sheet titleId="cozinha-titulo" title="Minha cozinha" icon={<KitchenIcon />} onClose={onClose} wide>
      <p className="sheet-note">
        Marque o que você tem e sabe fazer. A consultora só sugere o que a sua cozinha consegue e avisa quando um ingrediente não serve para o prato. O que você
        marcar aqui, ou contar na conversa, fica salvo no banco e aparece aqui.
      </p>
      <div className="cozinha-progresso">
        <div className="meter" role="img" aria-label={`${respondidos} de ${total} itens respondidos`}>
          <span style={{ width: `${total > 0 ? (respondidos / total) * 100 : 0}%` }} />
        </div>
        <small>{respondidos === 0 ? "Nada respondido ainda." : `${respondidos} de ${total} itens respondidos.`}</small>
      </div>

      <h3 className="sheet-label">Equipamentos</h3>
      <Chips itens={opcoes.equipamentos} form={form} onToggle={alternar} />
      {form.tem.fogao ? (
        <label className="field bocas">
          <span>Quantas bocas tem o seu fogão?</span>
          <select value={form.bocas ?? ""} onChange={(event) => mudar({ bocas: event.target.value === "" ? null : Number(event.target.value) })}>
            <option value="">Não sei dizer</option>
            {opcoes.bocas.map((n) => (
              <option key={n} value={n}>
                {n} {n === 1 ? "boca" : "bocas"}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <h3 className="sheet-label">Panelas e utensílios</h3>
      <Chips itens={opcoes.utensilios} form={form} onToggle={alternar} />
      <p className="sheet-note dica">O que ficar desmarcado é salvo como “não tenho”.</p>

      <h3 className="sheet-label">Habilidades</h3>
      <ul className="habilidades">
        {opcoes.habilidades.map((h) => {
          const nivel = form.habilidades[h.id];
          return (
            <li key={h.id} className="habilidade">
              <div className="habilidade-nome">
                <strong>{h.label}</strong>
                {h.dica ? <small>{h.dica}</small> : null}
              </div>
              <div className="niveis" role="radiogroup" aria-label={h.label}>
                {NIVEIS.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    role="radio"
                    aria-checked={nivel === n.id}
                    title={opcoes.niveis.find((o) => o.id === n.id)?.label}
                    className={nivel === n.id ? `nivel on ${n.id}` : "nivel"}
                    onClick={() => escolherNivel(h.id, nivel === n.id ? null : n.id)}
                  >
                    {n.curto}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="sheet-note dica">Toque de novo na resposta para voltar a “não respondi”.</p>

      <h3 className="sheet-label">Rotina da cozinha</h3>
      <div className="item-form">
        {opcoes.rotina.map((r) => {
          const atual = form.rotina[r.id];
          const conhecida = r.opcoes.some((o) => String(o.valor) === atual);
          return (
            <label key={r.id} className="field">
              <span>{r.label}</span>
              <select value={atual} onChange={(event) => mudar({ rotina: { ...form.rotina, [r.id]: event.target.value } })}>
                <option value="">Não respondi</option>
                {r.opcoes.map((o) => (
                  <option key={String(o.valor)} value={String(o.valor)}>
                    {o.label}
                  </option>
                ))}
                {atual !== "" && !conhecida ? <option value={atual}>{atual} (contado na conversa)</option> : null}
              </select>
            </label>
          );
        })}
      </div>

      {cozinha.outros.length > 0 ? (
        <>
          <h3 className="sheet-label">O que você contou na conversa</h3>
          <p className="sheet-note dica">A consultora guardou estas informações, que não estão nas listas acima. Apague o que estiver errado.</p>
          <ul className="outros">
            {cozinha.outros.map((o) => (
              <li key={`${o.grupo}:${o.chave}`}>
                <span>
                  <strong>{o.nome}</strong>
                  <small>
                    {ROTULO_DO_GRUPO[o.grupo]}
                    {o.grupo === "observacoes" ? "" : ` · ${o.texto}`}
                  </small>
                </span>
                <button type="button" className="mini" aria-label={`Apagar ${o.nome}`} disabled={ocupado} onClick={() => void apagar(o)}>
                  <CloseIcon />
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <div className="cozinha-rodape">
        {erro ? (
          <p className="sheet-note error" role="alert">
            {erro}
          </p>
        ) : null}
        <button type="button" className="primary" disabled={ocupado || (!sujo && respondidos > 0)} onClick={() => void salvar()}>
          {!sujo && respondidos > 0 ? "Tudo salvo" : "Salvar minha cozinha"}
        </button>
      </div>
    </Sheet>
  );
}
