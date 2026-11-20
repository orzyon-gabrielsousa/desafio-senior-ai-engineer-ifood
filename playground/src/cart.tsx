import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { AlertIcon, BookmarkIcon, CartIcon, CloseIcon, MinusIcon, PlusIcon, SparkIcon, TrashIcon } from "./icons";
import { brl, chave, lerNumero, quantidade } from "./lib/format";
import { agrupar, contarItens } from "./lib/loja";
import type { GrupoPrato } from "./lib/loja";
import { Sheet } from "./sheet";
import type { Acao, AlertaCozinha, CarrinhoAcoes, CompraHistorico, Estado, HistoricoCompras, ItemCarrinho, LinhaPrato, Salvo } from "./types";

const UNIDADES = ["un", "kg", "g", "L", "ml", "maço", "dúzia", "pacote"];
const MAX_LINHAS = 40;
const DATA_COMPRA = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short" });

/** Roda uma ação da tela e mostra o resultado no aviso fixo do painel; devolve true quando deu certo. */
type Executar = (acao: Acao, feito?: string) => Promise<boolean>;

type Comum = {
  ocupado: boolean;
  executar: Executar;
  acoes: CarrinhoAcoes;
};

function passo(item: ItemCarrinho): number {
  return item.unidade === "g" || item.unidade === "ml" ? 50 : 1;
}

function aComprar(linhas: LinhaPrato[]): number {
  return linhas.reduce((soma, linha) => soma + (linha.ja_tem ? 0 : linha.preco_total), 0);
}

/** O que a cozinha dela não faz (ou ainda não confirmou) para este prato ou ingrediente: vem da ficha em /prompts e do que ela marcou em Minha cozinha. */
function AvisoCozinha({ alerta, onCozinha }: { alerta: AlertaCozinha; onCozinha: () => void }) {
  return (
    <div className={`aviso-cozinha ${alerta.nivel}`} role="note">
      <AlertIcon />
      <div>
        {alerta.mensagens.map((mensagem) => (
          <p key={mensagem}>{mensagem}</p>
        ))}
        <button type="button" className="link-btn" onClick={onCozinha}>
          Abrir Minha cozinha
        </button>
      </div>
    </div>
  );
}

function Linha({
  nome,
  qtd,
  unidade,
  valor,
  jaTem,
  estimado = false,
  acao,
}: {
  nome: string;
  qtd: number;
  unidade: string | null;
  valor: number;
  jaTem: boolean;
  estimado?: boolean;
  acao?: ReactNode;
}) {
  return (
    <li className="linha">
      <span className="linha-nome">
        {nome}
        <small>
          {quantidade(qtd, unidade)}
          {estimado ? " · preço estimado" : ""}
        </small>
      </span>
      {jaTem ? <span className="selo">já tem</span> : null}
      <span className="linha-valor">{brl(valor)}</span>
      {acao ?? null}
    </li>
  );
}

function OrcamentoCard({ estado }: { estado: Estado | null }) {
  const o = estado?.orcamento;
  if (!o) return <p className="sheet-note error">Não consegui ler o carrinho. Confere se o make run está rodando (a API precisa estar no ar).</p>;
  const despesas = o.despesas ?? (o.comprometido + o.carrinho);
  const saldo = o.saldo_gerencial ?? o.saldo;
  const usado = o.inicial > 0 ? Math.min(100, Math.max(0, (despesas / o.inicial) * 100)) : 0;
  const { gasto_confirmado, reservado_cardapio, despesa_despensa } = o;
  return (
    <section className="budget-card" aria-label="Orçamento">
      <dl>
        <div>
          <dt>Orçamento</dt>
          <dd>{brl(o.inicial)}</dd>
        </div>
        <div>
          <dt>Despesas</dt>
          <dd>{brl(despesas)}</dd>
        </div>
        <div className={saldo <= 0 ? "saldo none" : "saldo"}>
          <dt>Saldo</dt>
          <dd>{brl(saldo)}</dd>
        </div>
      </dl>
      <div className="meter" role="img" aria-label={`${Math.round(usado)}% do orçamento contabilizado em despesas`}>
        <span style={{ width: `${usado}%` }} />
      </div>
      {despesa_despensa != null && despesa_despensa > 0 ? (
        <p className="budget-note">As despesas incluem {brl(despesa_despensa)} em ingredientes da despensa usados nos lotes.</p>
      ) : null}
      <details className="budget-purchases">
        <summary>Disponível para novas compras <strong>{brl(o.saldo)}</strong></summary>
        <dl>
          {gasto_confirmado != null && reservado_cardapio != null ? (
            <>
              <div><dt>Compras já pagas</dt><dd>{brl(gasto_confirmado)}</dd></div>
              <div><dt>Verba reservada para os lotes</dt><dd>{brl(reservado_cardapio)}</dd></div>
            </>
          ) : <div><dt>Compras lançadas ou comprometidas</dt><dd>{brl(o.comprometido)}</dd></div>}
          <div><dt>A comprar no carrinho</dt><dd>{brl(o.carrinho)}</dd></div>
        </dl>
      </details>
    </section>
  );
}

const STATUS_COMPRA: Record<CompraHistorico["status"], string> = {
  paga: "Paga",
  comprometida: "Reservada no orçamento",
  planejada: "Planejada",
  cancelada: "Cancelada",
};

const ORIGEM_INSUMO = {
  despensa: "Da despensa",
  complemento: "Complemento",
  preparo: "Recurso de preparo",
  ficha: "Na ficha aprovada",
};

const STATUS_LOTE = {
  preco_pendente: "Aguardando preço",
  no_cardapio: "No cardápio",
  encerrado: "Lote encerrado",
};

function ComprasRegistradas({ compras }: { compras: CompraHistorico[] }) {
  return (
    <ul className="linhas">
      {compras.map((compra) => (
        <li className="linha" key={compra.id}>
          <span className="linha-nome">
            {compra.ingrediente}
            <small>{[
              compra.quantidade != null ? quantidade(compra.quantidade, compra.unidade) : null,
              STATUS_COMPRA[compra.status],
              DATA_COMPRA.format(new Date(compra.registrado_em)),
            ].filter(Boolean).join(" · ")}</small>
          </span>
          <span className="linha-valor">{brl(compra.valor)}</span>
        </li>
      ))}
    </ul>
  );
}

function HistoricoAba({ historico }: { historico: HistoricoCompras | undefined }) {
  if (!historico?.lotes.length && !historico?.avulsas.length) {
    return <p className="sheet-note vazio">Os lotes aparecem aqui assim que você aceita a ficha na etapa 4. As compras registradas também ficam neste histórico.</p>;
  }
  return (
    <>
      {historico.lotes.map((lote) => (
        <section className="prato" key={lote.id} aria-label={`Lote de ${lote.prato}`}>
          <header className="prato-head">
            <div>
              <strong>{lote.prato}</strong>
              <small>{[
                lote.registrado_em ? DATA_COMPRA.format(new Date(lote.registrado_em)) : null,
                lote.porcoes != null ? quantidade(lote.porcoes, lote.porcoes === 1 ? "porção" : "porções") : null,
                STATUS_LOTE[lote.status ?? (lote.ativo ? "no_cardapio" : "encerrado")],
              ].filter(Boolean).join(" · ")}</small>
            </div>
            {lote.cmv_total != null ? <span className="prato-total"><small>Custo do lote</small>{brl(lote.cmv_total)}</span> : null}
          </header>
          {lote.insumos.length > 0 ? (
            <div className="historico-lista">
              <h3>Ingredientes do lote</h3>
              <ul className="linhas">
                {lote.insumos.map((insumo, posicao) => (
                  <li className="linha" key={posicao}>
                    <span className="linha-nome">
                      {insumo.ingrediente}
                      <small>{[
                        insumo.quantidade,
                        ORIGEM_INSUMO[insumo.origem],
                        insumo.derivado ? `Para preparar ${insumo.derivado}` : null,
                      ].filter(Boolean).join(" · ")}</small>
                    </span>
                    {insumo.custo_uso != null ? <span className="linha-valor">{brl(insumo.custo_uso)}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {lote.complementos.length > 0 ? (
            <div className="historico-lista">
              <h3>Compras de complementos</h3>
              <ComprasRegistradas compras={lote.complementos} />
            </div>
          ) : null}
        </section>
      ))}
      {historico.avulsas.length > 0 ? (
        <section className="prato historico-lista">
          <h3>Outras compras pagas</h3>
          <ComprasRegistradas compras={historico.avulsas} />
        </section>
      ) : null}
    </>
  );
}

/** Prato aberto: cada ingrediente com a quantidade e o valor, o total do prato e o que realmente sai do saldo. */
function PratoCard({
  grupo,
  salvo,
  ocupado,
  executar,
  acoes,
  alertas,
  onCozinha,
}: Comum & { grupo: GrupoPrato; salvo: boolean; alertas: AlertaCozinha[]; onCozinha: () => void }) {
  const parcial = grupo.aComprar < grupo.valor - 0.004;
  const linhas: LinhaPrato[] = grupo.linhas.map((linha) => ({
    nome: linha.nome,
    quantidade: linha.quantidade,
    unidade: linha.unidade,
    preco_total: linha.preco_total,
    ja_tem: linha.ja_tem,
  }));
  return (
    <section className="prato" aria-label={`Prato ${grupo.nome}`}>
      <header className="prato-head">
        <div>
          <strong>{grupo.nome}</strong>
          <small>
            {grupo.linhas.length} {grupo.linhas.length === 1 ? "ingrediente" : "ingredientes"}
            {parcial ? ` · você compra ${brl(grupo.aComprar)}, o resto já tem` : ""}
          </small>
        </div>
        <span className="prato-total"><small>Custo dos ingredientes</small>{brl(grupo.valor)}</span>
      </header>
      {alertas.map((alerta) => (
        <AvisoCozinha key={`${alerta.nivel}-${alerta.mensagens[0]}`} alerta={alerta} onCozinha={onCozinha} />
      ))}
      <ul className="linhas">
        {grupo.linhas.map((linha) => (
          <Linha
            key={linha.id}
            nome={linha.nome}
            qtd={linha.quantidade}
            unidade={linha.unidade}
            valor={linha.preco_total}
            jaTem={linha.ja_tem}
            estimado={linha.estimado}
            acao={linha.origem === "receita_aceita" ? undefined :
              <button
                type="button"
                className="mini"
                aria-label={`Tirar ${linha.nome} do prato`}
                disabled={ocupado}
                onClick={() => void executar(acoes.removeItem(linha.id))}
              >
                <CloseIcon />
              </button>
            }
          />
        ))}
      </ul>
      <div className="prato-acoes">
        <button
          type="button"
          className="mini-btn"
          disabled={ocupado}
          onClick={() => void executar(acoes.saveSalvo({ tipo: "prato", nome: grupo.nome, ingredientes: linhas }), `${grupo.nome} salvo em Salvos.`)}
        >
          <BookmarkIcon filled={salvo} />
          {salvo ? "Atualizar em Salvos" : "Salvar prato"}
        </button>
        <button
          type="button"
          className="mini-btn danger"
          disabled={ocupado}
          onClick={() => void executar(acoes.removePrato(grupo.nome), `${grupo.nome} saiu do carrinho.`)}
        >
          <TrashIcon />
          Tirar o prato
        </button>
      </div>
    </section>
  );
}

function ItemAvulso({
  item,
  salvo,
  ocupado,
  executar,
  acoes,
  alertas,
  onCozinha,
}: Comum & { item: ItemCarrinho; salvo: boolean; alertas: AlertaCozinha[]; onCozinha: () => void }) {
  return (
    <li className="cart-item">
      <div className="cart-item-main">
        <strong>{item.nome}</strong>
        <small>
          {quantidade(item.quantidade, item.unidade)}
          {item.estimado ? " · preço estimado" : ""}
          {item.motivo ? ` · ${item.motivo}` : ""}
        </small>
      </div>
      <span className="cart-price">{brl(item.preco_total)}</span>
      {alertas.map((alerta) => (
        <AvisoCozinha key={`${alerta.nivel}-${alerta.mensagens[0]}`} alerta={alerta} onCozinha={onCozinha} />
      ))}
      <div className="stepper">
        <button
          type="button"
          aria-label={salvo ? `${item.nome} já está salvo; atualizar` : `Salvar ${item.nome}`}
          title={salvo ? "Atualizar em Salvos" : "Salvar ingrediente"}
          disabled={ocupado}
          onClick={() =>
            void executar(
              acoes.saveSalvo({
                tipo: "ingrediente",
                nome: item.nome,
                quantidade: item.quantidade,
                unidade: item.unidade ?? "un",
                preco_total: item.preco_total,
              }),
              `${item.nome} salvo em Salvos.`,
            )
          }
        >
          <BookmarkIcon filled={salvo} />
        </button>
        <button
          type="button"
          aria-label={`Diminuir ${item.nome}`}
          disabled={ocupado || item.quantidade - passo(item) <= 0}
          onClick={() => void executar(acoes.setQuantidade(item.id, item.quantidade - passo(item)))}
        >
          <MinusIcon />
        </button>
        <button
          type="button"
          aria-label={`Aumentar ${item.nome}`}
          disabled={ocupado}
          onClick={() => void executar(acoes.setQuantidade(item.id, item.quantidade + passo(item)))}
        >
          <PlusIcon />
        </button>
        <button
          type="button"
          className="danger"
          aria-label={`Tirar ${item.nome} do carrinho`}
          disabled={ocupado}
          onClick={() => void executar(acoes.removeItem(item.id))}
        >
          <TrashIcon />
        </button>
      </div>
    </li>
  );
}

type FormProps = Comum & { avisar: (texto: string) => void };

/** Mostra por que o formulário não foi enviado e devolve null, para o validador sair na mesma linha. */
function recusar(avisar: (texto: string) => void, texto: string): null {
  avisar(texto);
  return null;
}

function FormIngrediente({ ocupado, executar, avisar, acoes }: FormProps) {
  const [nome, setNome] = useState("");
  const [qtd, setQtd] = useState("1");
  const [unidade, setUnidade] = useState("un");
  const [preco, setPreco] = useState("");
  const nomeRef = useRef<HTMLInputElement>(null);

  function ler() {
    const quantidadeNum = lerNumero(qtd);
    const precoNum = lerNumero(preco);
    if (!nome.trim()) return recusar(avisar, "Diga o nome do item.");
    if (!(quantidadeNum > 0)) return recusar(avisar, "A quantidade precisa ser maior que zero.");
    if (!(precoNum > 0)) return recusar(avisar, "Informe o preço do item em R$.");
    return { nome: nome.trim(), quantidade: quantidadeNum, unidade, preco_total: precoNum };
  }

  function limpar() {
    setNome("");
    setQtd("1");
    setPreco("");
    nomeRef.current?.focus();
  }

  async function adicionar(event: FormEvent) {
    event.preventDefault();
    const item = ler();
    if (item && (await executar(acoes.addItem(item), `${item.nome} entrou no carrinho.`))) limpar();
  }

  async function salvar() {
    const item = ler();
    if (item && (await executar(acoes.saveSalvo({ tipo: "ingrediente", ...item }), `${item.nome} salvo em Salvos.`))) limpar();
  }

  return (
    <form className="item-form" onSubmit={(event) => void adicionar(event)}>
      <label className="field wide">
        <span>Ingrediente</span>
        <input ref={nomeRef} value={nome} maxLength={80} placeholder="ex.: couve" onChange={(event) => setNome(event.target.value)} />
      </label>
      <label className="field">
        <span>Quantidade</span>
        <input value={qtd} inputMode="decimal" onChange={(event) => setQtd(event.target.value)} />
      </label>
      <label className="field">
        <span>Unidade</span>
        <select value={unidade} onChange={(event) => setUnidade(event.target.value)}>
          {UNIDADES.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </label>
      <label className="field wide">
        <span>Preço total (R$)</span>
        <input value={preco} inputMode="decimal" placeholder="ex.: 12,00" onChange={(event) => setPreco(event.target.value)} />
      </label>
      <div className="acoes-form wide">
        <button type="submit" className="primary" disabled={ocupado}>
          <PlusIcon />
          Pôr no carrinho
        </button>
        <button type="button" className="secondary" disabled={ocupado} onClick={() => void salvar()}>
          <BookmarkIcon />
          Só salvar
        </button>
      </div>
    </form>
  );
}

type LinhaForm = { chave: number; nome: string; qtd: string; unidade: string; preco: string; jaTem: boolean };

const novaLinha = (chaveLinha: number): LinhaForm => ({ chave: chaveLinha, nome: "", qtd: "1", unidade: "un", preco: "", jaTem: false });

function FormPrato({ saldo, ocupado, executar, avisar, acoes }: FormProps & { saldo: number | null }) {
  const proxima = useRef(3);
  const [nome, setNome] = useState("");
  const [linhas, setLinhas] = useState<LinhaForm[]>([novaLinha(1), novaLinha(2)]);

  const mudar = (chaveLinha: number, parte: Partial<LinhaForm>) =>
    setLinhas((atual) => atual.map((linha) => (linha.chave === chaveLinha ? { ...linha, ...parte } : linha)));
  const tirar = (chaveLinha: number) => setLinhas((atual) => (atual.length > 1 ? atual.filter((linha) => linha.chave !== chaveLinha) : atual));
  const incluir = () => setLinhas((atual) => (atual.length >= MAX_LINHAS ? atual : [...atual, novaLinha(proxima.current++)]));

  // Totais ao vivo: o prato inteiro e o que sai do saldo (o que ela já tem não gasta).
  const totais = linhas.reduce(
    (soma, linha) => {
      const valor = lerNumero(linha.preco);
      if (!Number.isFinite(valor) || valor <= 0) return soma;
      return { valor: soma.valor + valor, comprar: soma.comprar + (linha.jaTem ? 0 : valor) };
    },
    { valor: 0, comprar: 0 },
  );
  const passou = saldo !== null && totais.comprar > saldo + 0.004;

  function ler(): { prato: string; ingredientes: LinhaPrato[] } | null {
    const prato = nome.trim();
    if (!prato) return recusar(avisar, "Diga o nome do prato.");
    const ingredientes: LinhaPrato[] = [];
    for (const [indice, linha] of linhas.entries()) {
      if (!linha.nome.trim() && !linha.preco.trim()) continue; // linha em branco
      const rotulo = linha.nome.trim() || `Ingrediente ${indice + 1}`;
      if (!linha.nome.trim()) return recusar(avisar, `Ingrediente ${indice + 1}: diga o nome.`);
      const quantidadeNum = lerNumero(linha.qtd);
      if (!(quantidadeNum > 0)) return recusar(avisar, `${rotulo}: a quantidade precisa ser maior que zero.`);
      // O que ela já tem pode ficar sem valor; o que ela compra, não.
      const valor = linha.jaTem && !linha.preco.trim() ? 0 : lerNumero(linha.preco);
      if (!Number.isFinite(valor) || valor < 0 || (!linha.jaTem && valor <= 0)) return recusar(avisar, `${rotulo}: informe o valor em R$.`);
      ingredientes.push({ nome: linha.nome.trim(), quantidade: quantidadeNum, unidade: linha.unidade, preco_total: valor, ja_tem: linha.jaTem });
    }
    if (ingredientes.length === 0) return recusar(avisar, "Coloque ao menos um ingrediente, com o valor de cada um.");
    return { prato, ingredientes };
  }

  function limpar() {
    setNome("");
    setLinhas([novaLinha(proxima.current++), novaLinha(proxima.current++)]);
  }

  async function adicionar(event: FormEvent) {
    event.preventDefault();
    const dados = ler();
    if (dados && (await executar(acoes.addPrato(dados), `${dados.prato} entrou no carrinho, com os ingredientes.`))) limpar();
  }

  async function salvar() {
    const dados = ler();
    if (dados && (await executar(acoes.saveSalvo({ tipo: "prato", nome: dados.prato, ingredientes: dados.ingredientes }), `${dados.prato} salvo em Salvos.`))) limpar();
  }

  return (
    <form className="item-form" onSubmit={(event) => void adicionar(event)}>
      <label className="field wide">
        <span>Nome do prato</span>
        <input value={nome} maxLength={80} placeholder="ex.: coxinha de frango" onChange={(event) => setNome(event.target.value)} />
      </label>
      <div className="linhas-form wide">
        {linhas.map((linha, indice) => (
          <fieldset key={linha.chave} className="linha-form">
            <legend className="sr-only">Ingrediente {indice + 1}</legend>
            <div className="linha-form-topo">
              <label className="field">
                <span>Ingrediente {indice + 1}</span>
                <input
                  value={linha.nome}
                  maxLength={80}
                  placeholder="ex.: frango desfiado"
                  onChange={(event) => mudar(linha.chave, { nome: event.target.value })}
                />
              </label>
              <button
                type="button"
                className="mini"
                aria-label={`Tirar o ingrediente ${indice + 1}`}
                disabled={linhas.length === 1}
                onClick={() => tirar(linha.chave)}
              >
                <CloseIcon />
              </button>
            </div>
            <label className="field">
              <span>Quantidade</span>
              <input value={linha.qtd} inputMode="decimal" onChange={(event) => mudar(linha.chave, { qtd: event.target.value })} />
            </label>
            <label className="field">
              <span>Unidade</span>
              <select value={linha.unidade} onChange={(event) => mudar(linha.chave, { unidade: event.target.value })}>
                {UNIDADES.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Valor (R$)</span>
              <input
                value={linha.preco}
                inputMode="decimal"
                placeholder="ex.: 9,00"
                onChange={(event) => mudar(linha.chave, { preco: event.target.value })}
              />
            </label>
            <label className="check">
              <input type="checkbox" checked={linha.jaTem} onChange={(event) => mudar(linha.chave, { jaTem: event.target.checked })} />
              Já tenho na despensa (não gasta saldo)
            </label>
          </fieldset>
        ))}
        <button type="button" className="mini-btn" disabled={linhas.length >= MAX_LINHAS} onClick={incluir}>
          <PlusIcon />
          Ingrediente
        </button>
      </div>
      <p className="total-prato wide">
        <span>
          Valor do prato <b>{brl(totais.valor)}</b>
        </span>
        <span>
          Você compra <b className={passou ? "ruim" : ""}>{brl(totais.comprar)}</b>
        </span>
      </p>
      {passou ? (
        <p className="sheet-note error wide">
          Passa do saldo em {brl(totais.comprar - (saldo ?? 0))}. Marque o que você já tem ou tire um ingrediente.
        </p>
      ) : null}
      <div className="acoes-form wide">
        <button type="submit" className="primary" disabled={ocupado}>
          <PlusIcon />
          Pôr no carrinho
        </button>
        <button type="button" className="secondary" disabled={ocupado} onClick={() => void salvar()}>
          <BookmarkIcon />
          Só salvar
        </button>
      </div>
    </form>
  );
}

function SalvosAba({ salvos, ocupado, executar, acoes, aoColocar }: Comum & { salvos: Estado["salvos"]; aoColocar: () => void }) {
  async function colocar(salvo: Salvo) {
    if (await executar(acoes.salvoToCart(salvo.id), `${salvo.nome} foi para o carrinho.`)) aoColocar();
  }

  const botoes = (salvo: Salvo) => (
    <div className="salvo-acoes">
      <button type="button" className="mini-btn forte" disabled={ocupado} onClick={() => void colocar(salvo)}>
        <CartIcon />
        Pôr no carrinho
      </button>
      <button
        type="button"
        className="mini-btn danger"
        disabled={ocupado}
        onClick={() => void executar(acoes.removeSalvo(salvo.id), `${salvo.nome} foi apagado de Salvos.`)}
      >
        <TrashIcon />
        Apagar
      </button>
    </div>
  );

  if (salvos.pratos.length + salvos.ingredientes.length === 0) {
    return (
      <p className="sheet-note vazio">
        Nada salvo ainda. Use o marcador <BookmarkIcon /> num prato ou ingrediente do carrinho, ou preencha o formulário e escolha “Só salvar”. A consultora também
        salva quando você pedir.
      </p>
    );
  }

  return (
    <>
      <h3 className="sheet-label">Meus pratos ({salvos.pratos.length})</h3>
      {salvos.pratos.length === 0 ? <p className="sheet-note">Nenhum prato salvo.</p> : null}
      <ul className="salvos">
        {salvos.pratos.map((prato) => {
          const comprar = aComprar(prato.ingredientes);
          return (
            <li key={prato.id} className="salvo">
              <div className="salvo-head">
                <div>
                  <strong>{prato.nome}</strong>
                  <small>
                    {prato.ingredientes.length} {prato.ingredientes.length === 1 ? "ingrediente" : "ingredientes"}
                    {comprar < prato.preco_total - 0.004 ? ` · você compra ${brl(comprar)}` : ""}
                  </small>
                </div>
                <span className="cart-price">{brl(prato.preco_total)}</span>
              </div>
              <details className="salvo-detalhe">
                <summary>Ver ingredientes e valores</summary>
                <ul className="linhas">
                  {prato.ingredientes.map((linha) => (
                    <Linha key={`${linha.nome}-${linha.unidade ?? ""}`} nome={linha.nome} qtd={linha.quantidade} unidade={linha.unidade} valor={linha.preco_total} jaTem={linha.ja_tem} />
                  ))}
                </ul>
              </details>
              {botoes(prato)}
            </li>
          );
        })}
      </ul>

      <h3 className="sheet-label">Meus ingredientes ({salvos.ingredientes.length})</h3>
      {salvos.ingredientes.length === 0 ? <p className="sheet-note">Nenhum ingrediente salvo.</p> : null}
      <ul className="salvos">
        {salvos.ingredientes.map((item) => (
          <li key={item.id} className="salvo">
            <div className="salvo-head">
              <div>
                <strong>{item.nome}</strong>
                <small>{quantidade(item.quantidade ?? 1, item.unidade)}</small>
              </div>
              <span className="cart-price">{brl(item.preco_total)}</span>
            </div>
            {botoes(item)}
          </li>
        ))}
      </ul>
    </>
  );
}

type CartSheetProps = {
  estado: Estado | null;
  acoes: CarrinhoAcoes;
  onClose: () => void;
  onAsk: () => void;
  onCozinha: () => void;
  onPratos: () => void;
};

export function CartSheet({ estado, acoes, onClose, onAsk, onCozinha, onPratos }: CartSheetProps) {
  const [aba, setAba] = useState<"carrinho" | "historico" | "salvos">("carrinho");
  const [forma, setForma] = useState<"ingrediente" | "prato">("ingrediente");
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const itens = useMemo(() => estado?.carrinho ?? [], [estado]);
  const { pratos, avulsos } = useMemo(() => agrupar(itens), [itens]);
  const total = contarItens(itens);
  const salvos = estado?.salvos ?? { pratos: [], ingredientes: [] };
  const nomesSalvos = useMemo(
    () => ({ pratos: new Set(salvos.pratos.map((p) => chave(p.nome))), ingredientes: new Set(salvos.ingredientes.map((i) => chave(i.nome))) }),
    [salvos.pratos, salvos.ingredientes],
  );

  useEffect(() => {
    if (!erro && !aviso) return;
    const id = window.setTimeout(() => {
      setErro(null);
      setAviso(null);
    }, erro ? 8000 : 3500);
    return () => window.clearTimeout(id);
  }, [erro, aviso]);

  const executar: Executar = async (acao, feito) => {
    setOcupado(true);
    const falha = await acao;
    setErro(falha);
    setAviso(falha === null ? (feito ?? null) : null);
    setOcupado(false);
    return falha === null;
  };
  const avisar = (texto: string) => {
    setAviso(null);
    setErro(texto);
  };
  const comum: Comum = { ocupado, executar, acoes };
  const alertas = estado?.alertas ?? [];
  const historico = estado?.historico_compras;
  const temHistorico = Boolean(historico?.lotes.length || historico?.avulsas.length);

  return (
    <Sheet titleId="carrinho-titulo" title="Carrinho" icon={<CartIcon />} onClose={onClose} wide>
      <OrcamentoCard estado={estado} />

      <div className="tabs cart-tabs" role="tablist" aria-label="Carrinho">
        <button type="button" role="tab" aria-selected={aba === "carrinho"} className={aba === "carrinho" ? "tab on" : "tab"} onClick={() => setAba("carrinho")}>
          Carrinho <b>{total}</b>
        </button>
        <button type="button" role="tab" aria-selected={aba === "historico"} className={aba === "historico" ? "tab on" : "tab"} onClick={() => setAba("historico")}>
          Lotes e compras
        </button>
        <button type="button" role="tab" aria-selected={aba === "salvos"} className={aba === "salvos" ? "tab on" : "tab"} onClick={() => setAba("salvos")}>
          Salvos <b>{salvos.pratos.length + salvos.ingredientes.length}</b>
        </button>
      </div>

      {aba === "carrinho" ? (
        <>
          <button type="button" className="ask" onClick={onAsk}>
            <SparkIcon />
            Pedir sugestões à consultora
          </button>

          {total === 0 ? (
            <div className="sheet-note vazio">
              {temHistorico ? (
                <>
                  Seu carrinho está vazio. Consulte os lotes aprovados e as compras pagas no histórico.
                  <div><button type="button" className="link-btn" onClick={() => setAba("historico")}>Ver lotes e compras</button></div>
                </>
              ) : estado?.pratos_escolhidos?.length ? (
                <>
                  Seu carrinho está vazio. Acompanhe a ficha e os custos em Minha cozinha.
                  <div><button type="button" className="link-btn" onClick={onPratos}>Ver pratos atuais</button></div>
                </>
              ) : "O carrinho está vazio. Adicione um prato ou ingrediente abaixo, ou peça sugestões à consultora."}
            </div>
          ) : null}

          {pratos.length > 0 ? <h3 className="sheet-label">Pratos ({pratos.length})</h3> : null}
          {pratos.map((grupo) => (
            <PratoCard
              key={grupo.nome}
              grupo={grupo}
              salvo={nomesSalvos.pratos.has(chave(grupo.nome))}
              alertas={alertas.filter((alerta) => alerta.prato === grupo.nome)}
              onCozinha={onCozinha}
              {...comum}
            />
          ))}

          {avulsos.length > 0 ? (
            <>
              <h3 className="sheet-label">Ingredientes avulsos ({avulsos.length})</h3>
              <ul className="cart-list">
                {avulsos.map((item) => (
                  <ItemAvulso
                    key={item.id}
                    item={item}
                    salvo={nomesSalvos.ingredientes.has(chave(item.nome))}
                    alertas={alertas.filter((alerta) => alerta.item_id === item.id)}
                    onCozinha={onCozinha}
                    {...comum}
                  />
                ))}
              </ul>
            </>
          ) : null}
          {total > 0 ? (
            <button type="button" className="link-btn" disabled={ocupado} onClick={() => void executar(acoes.clear(), "Carrinho esvaziado.")}>
              Esvaziar o carrinho
            </button>
          ) : null}

          <h3 className="sheet-label">Adicionar ao carrinho</h3>
          <div className="tabs inline" role="tablist" aria-label="O que adicionar">
            <button type="button" role="tab" aria-selected={forma === "ingrediente"} className={forma === "ingrediente" ? "tab on" : "tab"} onClick={() => setForma("ingrediente")}>
              Ingrediente
            </button>
            <button type="button" role="tab" aria-selected={forma === "prato"} className={forma === "prato" ? "tab on" : "tab"} onClick={() => setForma("prato")}>
              Prato
            </button>
          </div>
          <div hidden={forma !== "ingrediente"}>
            <FormIngrediente avisar={avisar} {...comum} />
          </div>
          <div hidden={forma !== "prato"}>
            <FormPrato saldo={estado?.orcamento.saldo ?? null} avisar={avisar} {...comum} />
          </div>
        </>
      ) : aba === "historico" ? (
        <HistoricoAba historico={historico} />
      ) : (
        <SalvosAba salvos={salvos} aoColocar={() => setAba("carrinho")} {...comum} />
      )}

      {erro ? (
        <p className="toast error" role="alert">
          {erro}
        </p>
      ) : aviso ? (
        <p className="toast ok" role="status">
          {aviso}
        </p>
      ) : null}
    </Sheet>
  );
}
