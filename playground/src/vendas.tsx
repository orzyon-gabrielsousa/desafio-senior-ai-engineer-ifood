import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { estornarVenda, getVendas, registrarVenda, restaurarVenda } from "./lib/api";
import { brl, lerNumero, quantidade } from "./lib/format";
import type { NovaVenda, PeriodoVendas, PratoEscolhido, RespostaVendas, VendaReal } from "./types";
import "./vendas.css";

const PERIODOS: { id: PeriodoVendas; titulo: string }[] = [
  { id: "dia", titulo: "Hoje" },
  { id: "semana", titulo: "Esta semana" },
  { id: "mes", titulo: "Este mês" },
];
const DATA = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short" });
const DATA_HORA = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short" });

function erroDaVenda(error: unknown): string {
  return error instanceof Error ? error.message : "Não consegui atualizar as vendas. Tente de novo.";
}

function agoraLocal(): string {
  const agora = new Date();
  return new Date(agora.getTime() - agora.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

type Props = {
  periodoInicial?: PeriodoVendas;
  pratos?: PratoEscolhido[];
  versao: string;
  onAtualizadas?: () => void;
};

/** Vendas são fatos registrados pela pessoa; receita aprovada não conta como venda. */
export function VendasAnalytics({ periodoInicial = "dia", pratos = [], versao, onAtualizadas }: Props) {
  const [periodo, setPeriodo] = useState<PeriodoVendas>(periodoInicial);
  const [dados, setDados] = useState<RespostaVendas | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [mensagem, setMensagem] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [formulario, setFormulario] = useState(false);
  const [ocupado, setOcupado] = useState<number | null>(null);

  useEffect(() => { setPeriodo(periodoInicial); }, [periodoInicial]);
  useEffect(() => {
    let atual = true;
    let pedido = 0;
    function carregar() {
      const atualPedido = ++pedido;
      setDados((anterior) => anterior?.periodo === periodo ? anterior : null);
      setErro(null);
      getVendas(periodo).then((resposta) => {
        if (atual && atualPedido === pedido) setDados(resposta);
      }).catch((error: unknown) => {
        if (atual && atualPedido === pedido) setErro(erroDaVenda(error));
      });
    }
    carregar();
    window.addEventListener("focus", carregar);
    return () => { atual = false; window.removeEventListener("focus", carregar); };
  }, [periodo, versao, tentativa]);

  const aprovados = pratos.filter((prato) => prato.status === "aceito" && prato.preco_venda !== null);

  function recarregar() {
    setTentativa((valor) => valor + 1);
    onAtualizadas?.();
  }

  async function salvar(dadosDaVenda: NovaVenda): Promise<string | null> {
    try {
      const resposta = await registrarVenda(dadosDaVenda);
      const venda = resposta.venda;
      setMensagem(`${resposta.ja_registrada ? "Venda já registrada, sem duplicar" : "Venda registrada"}: ${quantidade(venda.quantidade, venda.quantidade === 1 ? "porção" : "porções")} de ${venda.prato}, total de ${brl(venda.faturamento_bruto)}.`);
      setFormulario(false);
      recarregar();
      return null;
    } catch (error) {
      // Confira a lista após uma falha de conexão: a resposta pode ter sido perdida,
      // enquanto o registro foi salvo. O formulário mantém a chave para o retry.
      recarregar();
      return erroDaVenda(error);
    }
  }

  async function corrigir(venda: VendaReal) {
    if (ocupado !== null) return;
    setOcupado(venda.id);
    setErro(null);
    try {
      if (venda.status === "estornada") {
        await restaurarVenda(venda.id);
        setMensagem(`Venda de ${venda.prato} restaurada nos totais.`);
      } else {
        await estornarVenda(venda.id);
        setMensagem(`Venda de ${venda.prato} estornada. Ela permanece no histórico e pode ser restaurada.`);
      }
      recarregar();
    } catch (error) {
      setErro(erroDaVenda(error));
    } finally {
      setOcupado(null);
    }
  }

  return (
    <section className="vendas-analytics" aria-labelledby="vendas-titulo">
      <header className="vendas-cabecalho">
        <div><h3 id="vendas-titulo">Vendas realizadas</h3><p>Somente vendas que você registrou.</p></div>
        <button type="button" className="mini-btn forte" disabled={!aprovados.length || formulario} onClick={() => {
          setMensagem(null);
          setFormulario(true);
        }}>Registrar venda</button>
      </header>
      <div className="vendas-periodos" role="group" aria-label="Período das vendas">
        {PERIODOS.map(({ id, titulo }) => <button type="button" key={id} className={periodo === id ? "vendas-periodo ativo" : "vendas-periodo"}
          aria-pressed={periodo === id} onClick={() => { setPeriodo(id); setMensagem(null); }}>{titulo}</button>)}
      </div>
      <p className="sheet-note vendas-fuso">Períodos no horário de São Paulo. A semana vai de segunda a domingo.</p>
      {mensagem ? <p className="vendas-sucesso" role="status">{mensagem}</p> : null}
      {formulario ? <FormularioVenda pratos={aprovados} onSave={salvar} onClose={() => setFormulario(false)} /> : null}
      {!aprovados.length ? <p className="sheet-note">Aprove um prato e escolha seu preço para registrar as vendas dele.</p> : null}
      {erro ? <div className="sheet-note error" role="alert">{erro}{" "}
        <button type="button" className="link-btn" onClick={() => setTentativa((valor) => valor + 1)}>Atualizar vendas</button>
      </div> : null}
      {dados === null ? !erro ? <p className="sheet-note" role="status">Carregando as vendas…</p> : null : <>
        <p className="vendas-intervalo">{DATA.format(new Date(dados.inicio))} a {DATA.format(new Date(new Date(dados.fim).getTime() - 1))}</p>
        <dl className="vendas-totais">
          <div className="vendas-total-principal"><dt>Vendas brutas</dt><dd>{brl(dados.resumo.faturamento_bruto)}</dd></div>
          <div><dt>Porções vendidas</dt><dd>{quantidade(dados.resumo.quantidade, null)}</dd></div>
          <div><dt>Taxa de 10%</dt><dd>{brl(dados.resumo.taxa_plataforma)}</dd></div>
          <div><dt>Repasse estimado (90%)</dt><dd>{brl(dados.resumo.recebe_liquido)}</dd></div>
          <div><dt>Custo estimado dos ingredientes</dt><dd>{brl(dados.resumo.cmv_total)}</dd></div>
          <div><dt>Lucro estimado</dt><dd>{brl(dados.resumo.lucro_estimado)}</dd></div>
        </dl>
        <p className="sheet-note">Lucro estimado = repasse de 90% menos o CMV registrado em cada venda. Os custos fixos não entram nesta conta. Vendas estornadas ficam fora dos totais.</p>
        <p className="sheet-note">Este registro acompanha vendas; ele não confirma produção ou compras e não movimenta automaticamente o estoque ou o orçamento de complementos.</p>
        {dados.vendas.length ? <details className="vendas-historico">
          <summary>Registros do período <span>{dados.total_registros}</span></summary>
          {dados.truncado ? <p className="sheet-note">Mostrando os {dados.limite} registros mais recentes de {dados.total_registros}. Os totais consideram todos os registros válidos do período.</p> : null}
          <ul className="vendas-lista" aria-label="Vendas do período">
            {dados.vendas.map((venda) => <li key={venda.id} className={venda.status === "estornada" ? "venda-linha estornada" : "venda-linha"}>
              <div className="venda-descricao"><strong>{venda.prato}</strong><span>{quantidade(venda.quantidade, venda.quantidade === 1 ? "porção" : "porções")} × {brl(venda.preco_unitario)}</span>
                <time dateTime={venda.vendido_em}>{DATA_HORA.format(new Date(venda.vendido_em))}</time>
                {venda.status === "estornada" ? <small className="venda-status">Estornada · fora dos totais</small> : null}</div>
              <div className="venda-valor"><strong>{brl(venda.faturamento_bruto)}</strong>
                <button type="button" className="link-btn" disabled={ocupado !== null} aria-label={`${venda.status === "estornada" ? "Restaurar" : "Estornar"} venda de ${venda.prato} em ${DATA_HORA.format(new Date(venda.vendido_em))}`}
                  onClick={() => void corrigir(venda)}>{ocupado === venda.id ? "Atualizando…" : venda.status === "estornada" ? "Restaurar" : "Estornar"}</button></div>
            </li>)}
          </ul>
        </details> : <p className="vendas-vazio">Nenhuma venda registrada neste período. Pratos planejados e aprovados não contam como vendas.</p>}
      </>}
    </section>
  );
}

function FormularioVenda({ pratos, onSave, onClose }: {
  pratos: PratoEscolhido[];
  onSave: (venda: NovaVenda) => Promise<string | null>;
  onClose: () => void;
}) {
  const [pratoId, setPratoId] = useState(pratos[0]?.id ?? 0);
  const [qtd, setQtd] = useState("1");
  const [preco, setPreco] = useState(() => String(pratos[0]?.preco_venda ?? "").replace(".", ","));
  const [dataHora, setDataHora] = useState(agoraLocal);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const operacao = useRef<{ fingerprint: string; id: string } | null>(null);
  const prato = pratos.find(({ id }) => id === pratoId);

  async function enviar(event: FormEvent) {
    event.preventDefault();
    if (salvando) return;
    const quantidadeVenda = Number(qtd);
    const valor = lerNumero(preco);
    const instante = new Date(dataHora);
    if (!prato || !Number.isSafeInteger(quantidadeVenda) || quantidadeVenda < 1 || !Number.isFinite(valor) || valor <= 0 || !Number.isFinite(instante.getTime())) {
      setErro("Escolha um prato aprovado, uma quantidade inteira e um preço positivo, com a data e hora da venda.");
      return;
    }
    if (Math.abs(valor * 100 - Math.round(valor * 100)) > 0.000001) {
      setErro("Informe o preço com até duas casas decimais.");
      return;
    }
    if (instante.getTime() > Date.now()) {
      setErro("A venda precisa ter sido realizada. Escolha uma data e hora até agora.");
      return;
    }
    const payload = { prato_id: pratoId, quantidade: quantidadeVenda, preco_unitario: valor, vendido_em: instante.toISOString() };
    const fingerprint = JSON.stringify(payload);
    if (operacao.current?.fingerprint !== fingerprint) operacao.current = { fingerprint, id: crypto.randomUUID() };
    setSalvando(true);
    setErro(null);
    const falha = await onSave({ ...payload, operacao: operacao.current.id });
    if (falha) setErro(`${falha} Confira os registros antes de alterar os dados; reenviar os mesmos valores não duplica a venda.`);
    setSalvando(false);
  }

  return (
    <form className="venda-form" onSubmit={(event) => void enviar(event)} aria-label="Registrar venda realizada">
      <h4>Registrar uma venda realizada</h4>
      <fieldset disabled={salvando}>
        <label className="field"><span>Prato vendido</span><select value={pratoId} onChange={(event) => {
          const id = Number(event.target.value);
          setPratoId(id);
          setPreco(String(pratos.find((item) => item.id === id)?.preco_venda ?? "").replace(".", ","));
        }}>{pratos.map((item) => <option key={item.id} value={item.id}>{item.nome}</option>)}</select></label>
        <div className="venda-form-par"><label className="field"><span>Porções vendidas</span><input type="number" min="1" step="1" required value={qtd} onChange={(event) => setQtd(event.target.value)} /></label>
          <label className="field"><span>Preço vendido por porção (R$)</span><input type="text" inputMode="decimal" required value={preco} onChange={(event) => setPreco(event.target.value)} /></label></div>
        <label className="field"><span>Data e hora da venda (horário do dispositivo)</span><input type="datetime-local" required value={dataHora} max={agoraLocal()} onChange={(event) => setDataHora(event.target.value)} /></label>
      </fieldset>
      {erro ? <p className="sheet-note error" role="alert">{erro}</p> : null}
      <div className="venda-form-acoes"><button type="button" className="mini-btn" disabled={salvando} onClick={onClose}>Cancelar</button>
        <button type="submit" className="mini-btn forte" disabled={salvando || !prato}>{salvando ? "Registrando…" : "Registrar venda realizada"}</button></div>
    </form>
  );
}
