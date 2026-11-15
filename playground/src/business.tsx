import { useState } from "react";
import type { FormEvent } from "react";
import { StoreIcon } from "./icons";
import { Sheet } from "./sheet";
import type { Acao, Estado, Negocio } from "./types";

// Sugestão de partida (nada é salvo até ela apertar "Salvar perfil"): restaurante de hotel que vende prato do dia, bolo e pizza.
const PADRAO: Negocio = {
  tipo: "restaurante_hotel",
  vende: ["prato_do_dia", "bolos", "pizzas"],
  publico: "",
  observacoes: "",
  estado: "SP",
  cidade: "São Paulo",
  zona: "sul",
};
// Perfil salvo antes de existir a localização não traz estes campos.
const SEM_LOCAL = { estado: "", cidade: "", zona: "" };

type BusinessSheetProps = {
  estado: Estado | null;
  onClose: () => void;
  onSave: (negocio: Negocio) => Acao;
};

export function BusinessSheet({ estado, onClose, onSave }: BusinessSheetProps) {
  const salvo = estado?.negocio ?? null;
  const [form, setForm] = useState<Negocio>(salvo ? { ...SEM_LOCAL, ...salvo } : PADRAO);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const alternar = (id: string) =>
    setForm((atual) => ({
      ...atual,
      vende: atual.vende.includes(id) ? atual.vende.filter((item) => item !== id) : [...atual.vende, id],
    }));

  const regiao = estado?.opcoes.estados.find((opcao) => opcao.id === form.estado)?.regiao;

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
      <p className="sheet-note">A consultora usa este perfil para sugerir pratos que vendem bem e os ingredientes do carrinho.</p>
      {salvo ? null : <p className="sheet-note dica">Sugestão inicial: ajuste o que quiser e salve.</p>}
      {estado ? (
        <form className="item-form" onSubmit={(event) => void enviar(event)}>
          <label className="field wide">
            <span>Tipo de negócio</span>
            <select value={form.tipo} onChange={(event) => setForm({ ...form, tipo: event.target.value })}>
              {estado.opcoes.tipos.map((opcao) => (
                <option key={opcao.id} value={opcao.id}>
                  {opcao.label}
                </option>
              ))}
            </select>
          </label>

          <h3 className="sheet-label wide">Onde fica a loja</h3>
          <p className="sheet-note dica wide">
            O Analytics e a consultora priorizam os pratos mais vendidos pela sua zona, depois pela cidade, pelo estado, pela região e pelo país.
          </p>
          <label className="field">
            <span>Estado</span>
            <select value={form.estado} onChange={(event) => setForm({ ...form, estado: event.target.value })}>
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
            <select value={form.zona} onChange={(event) => setForm({ ...form, zona: event.target.value })}>
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
            <input
              value={form.cidade}
              maxLength={60}
              placeholder="ex.: São Paulo"
              onChange={(event) => setForm({ ...form, cidade: event.target.value })}
            />
          </label>
          <p className="sheet-note dica wide">
            {regiao ? `Região: ${regiao}. ` : ""}
            {form.estado ? "" : "Sem o estado, o Analytics mostra só o ranking do país."}
          </p>

          <fieldset className="field wide chips-field">
            <legend>O que você vende</legend>
            <div className="toggle-chips">
              {estado.opcoes.vende.map((opcao) => (
                <button
                  key={opcao.id}
                  type="button"
                  className={form.vende.includes(opcao.id) ? "toggle on" : "toggle"}
                  aria-pressed={form.vende.includes(opcao.id)}
                  onClick={() => alternar(opcao.id)}
                >
                  {opcao.label}
                </button>
              ))}
            </div>
          </fieldset>
          <label className="field wide">
            <span>Para quem você vende</span>
            <input
              value={form.publico}
              maxLength={80}
              placeholder="ex.: hóspedes do hotel"
              onChange={(event) => setForm({ ...form, publico: event.target.value })}
            />
          </label>
          <label className="field wide">
            <span>Observações</span>
            <textarea
              value={form.observacoes}
              maxLength={400}
              rows={3}
              placeholder="ex.: almoço executivo, bolo e pizza no fim de semana"
              onChange={(event) => setForm({ ...form, observacoes: event.target.value })}
            />
          </label>
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
        <p className="sheet-note error">Não consegui ler o perfil. Confere se a API subiu com o make up.</p>
      )}
    </Sheet>
  );
}
