import { useEffect, useMemo, useState } from "react";
import { CheckIcon, CloseIcon, KitchenIcon, SparkIcon } from "./icons";
import { getPratosDaCozinha } from "./lib/api";
import { brl, quantidade } from "./lib/format";
import { Sheet } from "./sheet";
import type { Acao, Cozinha, Estado, Nivel, NovaCozinha, OpcaoCozinha, OpcoesCozinha, Outro, PratoDaCozinha, PratoEscolhido, PratosDaCozinha } from "./types";

// O que ela tem e sabe fazer, no mesmo perfil que a consultora lê e grava: o que ela conta na conversa aparece aqui,
// e o que ela marca aqui vale para a consultora, para o carrinho e para as travas do cardápio. A aba Pratos mostra, para cada
// prato da base, o que ele precisa (equipamentos, utensílios e habilidades) e se a cozinha dela tem.

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

const ROTULO_DO_STATUS: Record<PratoDaCozinha["status"], string> = {
  ok: "Dá para fazer",
  atencao: "Atenção: habilidade",
  confirmar: "A confirmar",
  nao_da: "Falta algo",
};

/** O prato com o que ele precisa: cada exigência com ✓ (tem), ✗ (falta) ou ? (ainda não respondeu), e as habilidades dela. */
function PratoLinha({ prato }: { prato: PratoDaCozinha }) {
  const falta = prato.precisa.filter((r) => r.estado === "falta").map((r) => r.nome);
  return (
    <li className="prato-cozinha">
      <div className="prato-cozinha-topo">
        <strong>{prato.prato}</strong>
        <span className={`selo-cozinha ${prato.status}`}>{prato.status === "nao_da" && falta.length > 0 ? `Falta ${falta.join(" e ")}` : ROTULO_DO_STATUS[prato.status]}</span>
      </div>
      <ul className="requisitos" aria-label={`O que ${prato.prato} precisa`}>
        {prato.precisa.map((r) => (
          <li key={r.nome} className={`req ${r.estado}`}>
            <span aria-hidden="true">{r.estado === "ok" ? "✓" : r.estado === "falta" ? "✗" : "?"}</span>
            {r.estado === "ok" && r.com ? r.com : r.nome}
            <span className="sr-only">{r.estado === "ok" ? " (você tem)" : r.estado === "falta" ? " (você não tem)" : " (ainda não respondeu)"}</span>
          </li>
        ))}
        {prato.habilidades.map((h) => (
          <li key={h.nome} className={`req hab ${h.nivel ?? "sem"}`}>
            {h.nome}
            <em>{h.nivel === "sim" ? " · sei" : h.nivel === "aprendendo" ? " · aprendendo" : h.nivel === "nao" ? " · não sei" : " · ?"}</em>
          </li>
        ))}
      </ul>
    </li>
  );
}

const SITUACAO_DO_PRATO: Record<PratoEscolhido["status"], { label: string; tom: string }> = {
  selecionado: { label: "A montar", tom: "confirmar" },
  cmv_calculado: { label: "Em precificação", tom: "atencao" },
  aceito: { label: "No cardápio", tom: "ok" },
};

function PratosEscolhidos({ pratos, onContinuar, onPesquisar }: {
  pratos: PratoEscolhido[];
  onContinuar: (prato: PratoEscolhido) => void;
  onPesquisar: () => void;
}) {
  return (
    <section className="pratos-escolhidos" aria-labelledby="pratos-escolhidos-titulo">
      <h3 id="pratos-escolhidos-titulo">Seus pratos</h3>
      {pratos.length === 0 ? (
        <div className="sheet-note vazio">
          Nenhum prato escolhido ainda. Escolha uma receita na conversa para acompanhar a ficha e o preço aqui.
          <div><button type="button" className="link-btn" onClick={onPesquisar}>Pesquisar receitas</button></div>
        </div>
      ) : (
        <ul className="escolhidos-lista">
          {pratos.map((prato) => {
            const situacao = prato.status === "selecionado" && prato.veredito === "viavel"
              ? { label: "Ficha conferida", tom: "ok" }
              : SITUACAO_DO_PRATO[prato.status];
            const fonte = prato.fonte_url && /^https?:\/\//i.test(prato.fonte_url) ? prato.fonte_url : null;
            const pendencias = prato.bloqueios?.length ? prato.bloqueios.map((bloqueio) => bloqueio.detalhe) : (prato.pendencias ?? []);
            return (
              <li key={prato.id} className="escolhido">
                <div className="prato-cozinha-topo">
                  <strong>{prato.nome}</strong>
                  <span className={`selo-cozinha ${situacao.tom}`}>{situacao.label}</span>
                </div>
                <dl className="escolhido-valores">
                  {prato.porcoes != null && <div><dt>Porções</dt><dd>{quantidade(prato.porcoes, null)}</dd></div>}
                  <div>
                    <dt>{prato.status === "selecionado" && prato.cmv_total != null ? "Custo estimado da receita" : "Custo da receita"}</dt>
                    <dd>{prato.cmv_total == null ? "A calcular" : brl(prato.cmv_total)}</dd>
                  </div>
                  {prato.cmv_porcao != null && <div><dt>Custo por porção</dt><dd>{brl(prato.cmv_porcao)}</dd></div>}
                  {prato.custo_da_despensa != null && <div><dt>Ingredientes da despensa</dt><dd>{brl(prato.custo_da_despensa)}</dd></div>}
                  {prato.compras_estimadas != null && <div><dt>Compras estimadas</dt><dd>{brl(prato.compras_estimadas)}</dd></div>}
                  {prato.preco_venda != null && <div><dt>Venda por porção</dt><dd>{brl(prato.preco_venda)}</dd></div>}
                </dl>
                {pendencias.length > 0 ? (
                  <ul className="escolhido-pendencias">
                    {pendencias.map((pendencia, posicao) => <li key={posicao}>{pendencia}</li>)}
                  </ul>
                ) : null}
                <div className="escolhido-acoes">
                  <button type="button" className="mini-btn forte" onClick={() => onContinuar(prato)} aria-label={`Continuar no chat com ${prato.nome}`}>
                    <SparkIcon /> Continuar no chat
                  </button>
                  {fonte && <a className="link-btn" href={fonte} target="_blank" rel="noreferrer">Ver receita de referência</a>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function PratosAba({ dados, erro, preenchida }: { dados: PratosDaCozinha | null; erro: string | null; preenchida: boolean }) {
  if (erro) {
    return (
      <p className="sheet-note error" role="alert">
        {erro}
      </p>
    );
  }
  if (!dados) return <p className="sheet-note">Carregando os pratos…</p>;
  const { resumo } = dados;
  return (
    <>
      <p className="sheet-note">
        Cada prato mostra o que ele precisa e se a sua cozinha tem. <b>✓</b> você tem, <b>✗</b> falta, <b>?</b> você ainda não respondeu.
        {preenchida ? "" : " Marque o que você tem e sabe fazer na outra aba para ver quais pratos você consegue fazer."}
      </p>
      <p className="pratos-resumo" aria-label="Resumo dos pratos">
        <span className="selo-cozinha ok">Dá para fazer {resumo.ok}</span>
        <span className="selo-cozinha atencao">Atenção {resumo.atencao}</span>
        <span className="selo-cozinha confirmar">A confirmar {resumo.confirmar}</span>
        <span className="selo-cozinha nao_da">Falta algo {resumo.nao_da}</span>
        <small>de {resumo.total} pratos da base</small>
      </p>
      {dados.categorias.map((categoria) => {
        const faz = categoria.pratos.filter((p) => p.status === "ok").length;
        return (
          <details key={categoria.nome} className="categoria-pratos" open={categoria.da_loja}>
            <summary>
              <strong>{categoria.nome}</strong>
              <small>
                {categoria.da_loja ? "o que você faz · " : ""}
                {faz} de {categoria.pratos.length} dão para fazer
              </small>
            </summary>
            <ul className="pratos-cozinha">
              {categoria.pratos.map((prato) => (
                <PratoLinha key={prato.prato} prato={prato} />
              ))}
            </ul>
          </details>
        );
      })}
    </>
  );
}

type KitchenSheetProps = {
  estado: Estado | null;
  abaInicial?: "cozinha" | "pratos";
  onClose: () => void;
  onSave: (dados: NovaCozinha) => Acao;
  onContinuar: (prato: PratoEscolhido) => void;
  onPesquisar: () => void;
};

export function KitchenSheet({ estado, abaInicial = "cozinha", onClose, onSave, onContinuar, onPesquisar }: KitchenSheetProps) {
  const cozinha = estado?.cozinha ?? null;
  const opcoes = estado?.opcoes.cozinha ?? null;
  const escolhidos = estado?.pratos_escolhidos ?? [];
  // O formulário é derivado do que está salvo: enquanto ela não mexe, acompanha o que a conversa gravar; mexeu, vale o que ela editou.
  const inicial = useMemo(() => (cozinha && opcoes ? doEstado(cozinha, opcoes) : null), [cozinha, opcoes]);
  const [editado, setEditado] = useState<Form | null>(null);
  const [aba, setAba] = useState(abaInicial);
  const [erro, setErro] = useState<string | null>(null);
  const [salvou, setSalvou] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [pratos, setPratos] = useState<PratosDaCozinha | null>(null);
  const [erroPratos, setErroPratos] = useState<string | null>(null);

  // Os pratos dependem do que ela tem e sabe (e do tipo de alimentação do negócio, que abre as categorias dela primeiro): recarrega a cada mudança.
  const chave = JSON.stringify([cozinha, estado?.negocio?.alimentacao ?? []]);
  useEffect(() => {
    let atual = true;
    getPratosDaCozinha()
      .then((resposta) => {
        if (!atual) return;
        setPratos(resposta);
        setErroPratos(null);
      })
      .catch((falha: unknown) => {
        if (atual) setErroPratos(falha instanceof Error ? falha.message : "Não consegui ler os pratos.");
      });
    return () => {
      atual = false;
    };
  }, [chave]);

  if (!cozinha || !opcoes || !inicial) {
    return (
      <Sheet titleId="cozinha-titulo" title="Minha cozinha" icon={<KitchenIcon />} onClose={onClose} wide>
        <p className="sheet-note error">Não consegui ler a cozinha. Confere se o make run está rodando (a API precisa estar no ar).</p>
      </Sheet>
    );
  }

  const form = editado ?? inicial;
  const sujo = editado !== null && JSON.stringify(editado) !== JSON.stringify(inicial);
  const { respondidos, total } = cozinha.resumo;
  const mudar = (parte: Partial<Form>) => {
    setSalvou(false);
    setEditado({ ...form, ...parte });
  };
  const alternar = (id: string) => mudar({ tem: { ...form.tem, [id]: !form.tem[id] }, ...(id === "fogao" && form.tem.fogao ? { bocas: null } : {}) });
  const escolherNivel = (id: string, nivel: Nivel | null) => mudar({ habilidades: { ...form.habilidades, [id]: nivel } });

  async function salvar() {
    if (!opcoes || !inicial) return;
    setOcupado(true);
    const falha = await onSave(paraEnvio(form, inicial, opcoes));
    setOcupado(false);
    setErro(falha);
    if (falha === null) {
      setEditado(null);
      setSalvou(true);
    }
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

      <div className="tabs" role="tablist" aria-label="Minha cozinha">
        <button type="button" role="tab" aria-selected={aba === "cozinha"} className={aba === "cozinha" ? "tab on" : "tab"} onClick={() => setAba("cozinha")}>
          O que eu tenho e sei
        </button>
        <button type="button" role="tab" aria-selected={aba === "pratos"} className={aba === "pratos" ? "tab on" : "tab"} onClick={() => setAba("pratos")}>
          Pratos {escolhidos.length ? <b aria-label={`${escolhidos.length} pratos escolhidos`}>{escolhidos.length}</b> : null}
        </button>
      </div>

      {aba === "pratos" ? (
        <>
          <PratosEscolhidos pratos={escolhidos} onContinuar={onContinuar} onPesquisar={onPesquisar} />
          <h3 className="sheet-label">Catálogo da cozinha</h3>
          <PratosAba dados={pratos} erro={erroPratos} preenchida={respondidos > 0} />
        </>
      ) : (
        <>
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
            ) : salvou ? (
              <p className="sheet-note ok" role="status">
                Cozinha salva. Veja na aba Pratos o que dá para fazer.
              </p>
            ) : null}
            <button type="button" className="primary" disabled={ocupado || (!sujo && respondidos > 0)} onClick={() => void salvar()}>
              {!sujo && respondidos > 0 ? "Tudo salvo" : "Salvar minha cozinha"}
            </button>
          </div>
        </>
      )}
    </Sheet>
  );
}
