import { useEffect, useMemo, useState } from "react";
import { CheckIcon, CloseIcon, KitchenIcon, SparkIcon } from "./icons";
import { IngredientesFicha } from "./ingredientes-ficha";
import { getPratosDaCozinha } from "./lib/api";
import { brl, chave, quantidade } from "./lib/format";
import { fichaPrecisaDaFonte, pendenciasDoPrato, situacaoDoPrato } from "./lib/pratos";
import { Sheet } from "./sheet";
import type { Acao, Cozinha, Estado, Nivel, NovaCozinha, OpcaoCozinha, OpcoesCozinha, Outro, PratoDaCozinha, PratoEscolhido, PratosDaCozinha } from "./types";

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

function PratosEscolhidos({ pratos, jornada, onContinuar, onPesquisar }: {
  pratos: PratoEscolhido[];
  jornada: Estado["jornada"];
  onContinuar: (prato: PratoEscolhido) => void;
  onPesquisar: () => void;
}) {
  return (
    <section className="pratos-escolhidos" aria-labelledby="pratos-escolhidos-titulo">
      <h3 id="pratos-escolhidos-titulo">Pratos escolhidos</h3>
      {pratos.length === 0 ? (
        <div className="sheet-note vazio">
          Nenhum prato escolhido ainda. Escolha uma receita na conversa para acompanhar a ficha e o preço aqui.
          <div><button type="button" className="link-btn" onClick={onPesquisar}>Pesquisar receitas</button></div>
        </div>
      ) : (
        <ul className="escolhidos-lista">
          {pratos.map((prato) => {
            const situacao = situacaoDoPrato(prato);
            const fonte = prato.fonte_url && /^https?:\/\//i.test(prato.fonte_url) ? prato.fonte_url : null;
            const pendencias = pendenciasDoPrato(prato);
            const pergunta = jornada?.prato_id === prato.id ? jornada.pergunta?.texto : null;
            const conferirFonte = fichaPrecisaDaFonte(prato);
            const adiado = jornada?.controle?.rascunhos_adiados?.includes(prato.id) === true;
            const acao = adiado ? "Retomar montagem" : pergunta ? "Responder no chat" : situacao.acao;
            return (
              <li key={prato.id} className="escolhido">
                <div className="prato-cozinha-topo">
                  <strong>{prato.nome}</strong>
                  <span className={`selo-cozinha ${adiado ? "confirmar" : situacao.tom}`}>{adiado ? "Guardado para depois" : situacao.titulo}</span>
                </div>
                <p className="escolhido-proximo">{adiado ? "A ficha está salva. Você pode retomar a montagem quando quiser." : situacao.descricao}</p>
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
                <IngredientesFicha ingredientes={prato.ingredientes_da_ficha} aberto />
                {prato.ingredientes_faltantes?.length ? <div className="sheet-note" aria-label={`Observações de ingredientes de ${prato.nome}`}>
                  <strong>Observações salvas na ficha</strong>
                  <ul>{prato.ingredientes_faltantes.map((nota) => <li key={nota.ingrediente}>{nota.observacao}</li>)}</ul>
                </div> : null}
                {pendencias.length > 0 ? (
                  <div className="escolhido-pendencias">
                    {pergunta ? <p><strong>Próxima pergunta:</strong> {pergunta}</p>
                      : !conferirFonte ? <p><strong>{adiado ? "Para conferir ao retomar" : "Próxima pendência"}</strong> · {pendencias[0]}</p> : null}
                    {conferirFonte || pendencias.length > 1 ? <details><summary>{conferirFonte ? "Ver o que falta conferir na receita" : `Ver mais ${pendencias.length - 1} ${pendencias.length === 2 ? "pendência" : "pendências"}`}</summary>
                      <ul>{pendencias.slice(conferirFonte ? 0 : 1).map((pendencia, posicao) => <li key={posicao}>{pendencia}</li>)}</ul>
                    </details> : null}
                  </div>
                ) : null}
                <div className="escolhido-acoes">
                  <button type="button" className="mini-btn forte" onClick={() => onContinuar(prato)} aria-label={`${acao}: ${prato.nome}`}>
                    <SparkIcon /> {acao}
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

function PratosPossiveis({ dados, erro, preenchida, escolhidos }: {
  dados: PratosDaCozinha | null;
  erro: string | null;
  preenchida: boolean;
  escolhidos: PratoEscolhido[];
}) {
  if (erro) {
    return (
      <p className="sheet-note error" role="alert">
        {erro}
      </p>
    );
  }
  if (!dados) return <p className="sheet-note" role="status">Carregando as opções do catálogo…</p>;
  const nomesEscolhidos = new Set(escolhidos.map(({ nome }) => chave(nome)));
  const categorias = dados.categorias.map((categoria) => ({
    ...categoria,
    pratos: categoria.pratos.filter(({ prato }) => !nomesEscolhidos.has(chave(prato))),
  })).filter(({ pratos }) => pratos.length > 0);
  const opcoes = categorias.flatMap(({ pratos }) => pratos);
  const totalPorStatus = (status: PratoDaCozinha["status"]) => opcoes.filter((prato) => prato.status === status).length;
  return (
    <>
      <p className="sheet-note">
        Compare opções do catálogo com os equipamentos e habilidades da sua cozinha. <b>✓</b> você tem, <b>✗</b> falta, <b>?</b> a conferir.
        {preenchida ? "" : " Marque o que você tem e sabe fazer na outra aba para ver quais pratos você consegue fazer."}
      </p>
      <p className="pratos-resumo" aria-label="Resumo das opções do catálogo">
        <span className="selo-cozinha ok">Dá para fazer {totalPorStatus("ok")}</span>
        <span className="selo-cozinha atencao">Atenção {totalPorStatus("atencao")}</span>
        <span className="selo-cozinha confirmar">A confirmar {totalPorStatus("confirmar")}</span>
        <span className="selo-cozinha nao_da">Falta algo {totalPorStatus("nao_da")}</span>
      </p>
      {categorias.length === 0 ? <p className="sheet-note">Nenhuma opção adicional no catálogo.</p> : null}
      {categorias.map((categoria) => {
        const faz = categoria.pratos.filter((p) => p.status === "ok").length;
        return (
          <details key={categoria.nome} className="categoria-pratos" open={categoria.da_loja}>
            <summary>
              <strong>{categoria.nome}</strong>
              <small>
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

const ABAS_COZINHA = [
  { id: "cozinha", titulo: "Equipamentos e habilidades" },
  { id: "pratos", titulo: "Pratos atuais" },
  { id: "possiveis", titulo: "Pratos possíveis" },
] as const;

export function KitchenSheet({ estado, abaInicial = "cozinha", onClose, onSave, onContinuar, onPesquisar }: KitchenSheetProps) {
  const cozinha = estado?.cozinha ?? null;
  const opcoes = estado?.opcoes.cozinha ?? null;
  const escolhidos = estado?.pratos_escolhidos ?? [];
  // O formulário é derivado do que está salvo: enquanto ela não mexe, acompanha o que a conversa gravar; mexeu, vale o que ela editou.
  const inicial = useMemo(() => (cozinha && opcoes ? doEstado(cozinha, opcoes) : null), [cozinha, opcoes]);
  const [editado, setEditado] = useState<Form | null>(null);
  const [aba, setAba] = useState<(typeof ABAS_COZINHA)[number]["id"]>(abaInicial);
  const [erro, setErro] = useState<string | null>(null);
  const [salvou, setSalvou] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [pratos, setPratos] = useState<PratosDaCozinha | null>(null);
  const [erroPratos, setErroPratos] = useState<string | null>(null);

  // Os pratos dependem do que ela tem e sabe (e do tipo de alimentação do negócio, que abre as categorias dela primeiro): recarrega a cada mudança.
  const chaveCatalogo = JSON.stringify([cozinha, estado?.negocio?.alimentacao ?? []]);
  useEffect(() => { setAba(abaInicial); }, [abaInicial]);
  useEffect(() => {
    if (aba !== "possiveis") return;
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
  }, [aba, chaveCatalogo]);

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
  const faixaDeTempo = cozinha.outros.find(({ grupo, chave }) => grupo === "restricoes" && chave === "tempo_por_cozinhada_faixa");
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
      <div className="tabs cozinha-abas" role="tablist" aria-label="Minha cozinha">
        {ABAS_COZINHA.map(({ id, titulo }) => (
          <button key={id} id={`cozinha-aba-${id}`} aria-controls={`cozinha-painel-${id}`} type="button" role="tab"
            aria-selected={aba === id} className={aba === id ? "tab on" : "tab"} onClick={() => setAba(id)}>
            {titulo} {id === "pratos" && escolhidos.length ? <b aria-label={`${escolhidos.length} pratos escolhidos`}>{escolhidos.length}</b> : null}
          </button>
        ))}
      </div>

      <div id="cozinha-painel-pratos" role="tabpanel" aria-labelledby="cozinha-aba-pratos" hidden={aba !== "pratos"}>
        <PratosEscolhidos pratos={escolhidos} jornada={estado?.jornada} onContinuar={onContinuar} onPesquisar={onPesquisar} />
      </div>
      <div id="cozinha-painel-possiveis" role="tabpanel" aria-labelledby="cozinha-aba-possiveis" hidden={aba !== "possiveis"}>
        {aba === "possiveis" ? <PratosPossiveis dados={pratos} erro={erroPratos} preenchida={respondidos > 0} escolhidos={escolhidos} /> : null}
      </div>
      <div id="cozinha-painel-cozinha" role="tabpanel" aria-labelledby="cozinha-aba-cozinha" hidden={aba !== "cozinha"}>
          <p className="sheet-note">Marque o que você tem e sabe fazer para a consultora conferir os pratos escolhidos.</p>
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
              const faixaInformada = r.id === "tempo_por_cozinhada_min" && atual === "" ? faixaDeTempo : undefined;
              return (
                <label key={r.id} className="field">
                  <span>{r.label}</span>
                  <select value={atual} onChange={(event) => mudar({ rotina: { ...form.rotina, [r.id]: event.target.value } })}>
                    <option value="">{faixaInformada ? "Faixa informada no chat" : "Não respondi"}</option>
                    {r.opcoes.map((o) => (
                      <option key={String(o.valor)} value={String(o.valor)}>
                        {o.label}
                      </option>
                    ))}
                    {atual !== "" && !conhecida ? <option value={atual}>{atual} (contado na conversa)</option> : null}
                  </select>
                  {faixaInformada ? <small>Informado no chat: {faixaInformada.texto}. Você pode manter essa resposta ou escolher outro tempo.</small> : null}
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
                Cozinha salva. Veja em Pratos possíveis o que dá para fazer.
              </p>
            ) : null}
            <button type="button" className="primary" disabled={ocupado || (!sujo && respondidos > 0)} onClick={() => void salvar()}>
              {!sujo && respondidos > 0 ? "Tudo salvo" : "Salvar minha cozinha"}
            </button>
          </div>
      </div>
    </Sheet>
  );
}
