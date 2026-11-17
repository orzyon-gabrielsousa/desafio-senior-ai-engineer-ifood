import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { StoreIcon } from "./icons";
import { Sheet } from "./sheet";
import type { Acao, Estado, Negocio } from "./types";

// Meu negócio: onde a loja fica, que tipo de negócio é e que tipo de alimentação ela faz. Os pratos ficam em Minha cozinha.
// A consultora pergunta isso na primeira conversa e grava no mesmo registro: o que ela contou aparece aqui.
const VAZIO: Negocio = { tipo: "", alimentacao: [], pais: "Brasil", estado: "", cidade: "", zona: "" };

type BusinessSheetProps = {
  estado: Estado | null;
  onClose: () => void;
  onSave: (negocio: Negocio) => Acao;
};

export function BusinessSheet({ estado, onClose, onSave }: BusinessSheetProps) {
  const salvo = estado?.negocio ?? null;
  // O formulário acompanha o que está salvo (inclusive o que a conversa gravou) até ela mexer.
  const inicial = useMemo<Negocio>(() => (salvo ? { ...VAZIO, ...salvo, alimentacao: salvo.alimentacao ?? [] } : VAZIO), [salvo]);
  const [editado, setEditado] = useState<Negocio | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const form = editado ?? inicial;

  const mudar = (parte: Partial<Negocio>) => setEditado({ ...form, ...parte });
  const alternar = (id: string) =>
    mudar({ alimentacao: form.alimentacao.includes(id) ? form.alimentacao.filter((item) => item !== id) : [...form.alimentacao, id] });

  const regiao = estado?.opcoes.estados.find((opcao) => opcao.id === form.estado)?.regiao;
  const paises = estado?.opcoes.paises ?? [];

  async function enviar(event: FormEvent) {
    event.preventDefault();
    setOcupado(true);
    const falha = await onSave(form);
    setOcupado(false);
    setErro(falha);
    if (falha === null) onClose();
  }

  return (
    <Sheet titleId="negocio-titulo" title="Meu negócio" icon={<StoreIcon />} onClose={onClose}>
      <p className="sheet-note">
        A consultora usa seu perfil para pesquisar receitas para o delivery. Você pode preencher aqui ou responder na conversa. O que você contar aparece neste perfil.
      </p>
      {salvo ? null : <p className="sheet-note dica">Nada salvo ainda. A consultora pergunta na primeira conversa, ou preencha abaixo.</p>}
      {estado ? (
        <form className="item-form" onSubmit={(event) => void enviar(event)}>
          <label className="field wide">
            <span>Tipo de negócio</span>
            <select value={form.tipo} onChange={(event) => mudar({ tipo: event.target.value })}>
              <option value="">Não informar</option>
              {estado.opcoes.tipos.map((opcao) => (
                <option key={opcao.id} value={opcao.id}>
                  {opcao.label}
                </option>
              ))}
            </select>
          </label>

          <fieldset className="field wide chips-field">
            <legend>Tipo de alimentação que você faz</legend>
            <div className="toggle-chips">
              {estado.opcoes.alimentacao.map((opcao) => (
                <button
                  key={opcao.id}
                  type="button"
                  className={form.alimentacao.includes(opcao.id) ? "toggle on" : "toggle"}
                  aria-pressed={form.alimentacao.includes(opcao.id)}
                  onClick={() => alternar(opcao.id)}
                >
                  {opcao.label}
                </button>
              ))}
            </div>
          </fieldset>

          <h3 className="sheet-label wide">Onde fica a loja</h3>
          <p className="sheet-note dica wide">
            Informe onde você vende para a consultora procurar receitas da região e dados de pedidos da sua área.
          </p>
          <label className="field wide">
            <span>País</span>
            <select value={form.pais} disabled={paises.length < 2} onChange={(event) => mudar({ pais: event.target.value })}>
              {paises.map((opcao) => (
                <option key={opcao.id} value={opcao.id}>
                  {opcao.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Estado</span>
            <select value={form.estado} onChange={(event) => mudar({ estado: event.target.value })}>
              <option value="">Não informar</option>
              {estado.opcoes.estados.map((opcao) => (
                <option key={opcao.id} value={opcao.id}>
                  {opcao.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Zona da cidade</span>
            <select value={form.zona} onChange={(event) => mudar({ zona: event.target.value })}>
              <option value="">Não informar</option>
              {estado.opcoes.zonas.map((opcao) => (
                <option key={opcao.id} value={opcao.id}>
                  {opcao.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field wide">
            <span>Cidade</span>
            <input value={form.cidade} maxLength={60} placeholder="ex.: São Paulo" onChange={(event) => mudar({ cidade: event.target.value })} />
          </label>
          {regiao ? <p className="sheet-note dica wide">Região: {regiao}</p> : null}

          <button type="submit" className="primary wide" disabled={ocupado}>
            Salvar perfil
          </button>
          {erro ? (
            <p className="sheet-note error wide" role="alert">
              {erro}
            </p>
          ) : null}
        </form>
      ) : (
        <p className="sheet-note error">Não consegui ler o perfil. Confere se o make run está rodando (a API precisa estar no ar).</p>
      )}
    </Sheet>
  );
}
