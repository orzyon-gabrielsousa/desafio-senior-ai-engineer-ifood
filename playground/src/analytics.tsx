import { useEffect, useMemo, useState } from "react";
import { ChartIcon, KitchenIcon, SparkIcon, StoreIcon } from "./icons";
import { getAnalytics, getPrecos } from "./lib/api";
import { brl, lerNumero } from "./lib/format";
import { Sheet } from "./sheet";
import type { Analytics, Cenario, EscopoId, Estado, Local, MaisVendido, QuantoCobrar, Visao } from "./types";

const ABAS: { id: Visao; rotulo: string }[] = [
  { id: "prioridade", rotulo: "Para a sua loja" },
  { id: "zona", rotulo: "Zona" },
  { id: "cidade", rotulo: "Cidade" },
  { id: "estado", rotulo: "Estado" },
  { id: "regiao", rotulo: "Região" },
  { id: "pais", rotulo: "País" },
];

const ROTULO: Record<EscopoId, string> = { zona: "Zona", cidade: "Cidade", estado: "Estado", regiao: "Região", pais: "País" };

const TAXA_PADRAO = 0.1;

function textoDoLocal(local: Local): string {
  const lugar = local.cidade && local.estado ? `${local.cidade}/${local.estado}` : local.estado_nome;
  return [[local.zona, lugar].filter(Boolean).join(", "), local.regiao ? `região ${local.regiao}` : ""].filter(Boolean).join(" · ");
}

const pct = (valor: number) => `${Math.round(valor * 100)}%`;
// 0,90 e 0,63: sempre com duas casas, como a regra da casa ("0,90 × P") que aparece na lateral
const numero = (valor: number) => valor.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 3 });

type AnalyticsSheetProps = {
  estado: Estado | null;
  onClose: () => void;
  onNegocio: () => void;
  onCozinha: () => void;
  /** Preenche a conversa para a consultora detalhar os ingredientes do prato escolhido. */
  onAsk: (prato: string) => void;
};

export function AnalyticsSheet({ estado, onClose, onNegocio, onCozinha, onAsk }: AnalyticsSheetProps) {
  const [visao, setVisao] = useState<Visao>("prioridade");
  const [categoria, setCategoria] = useState<string | null>(null);
  const [soDaPraFazer, setSoDaPraFazer] = useState(false);
  const [margem, setMargem] = useState(0);
  const [dados, setDados] = useState<Analytics | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    let atual = true;
    setCarregando(true);
    getAnalytics(visao)
      .then((resposta) => {
        if (!atual) return;
        setDados(resposta);
        setErro(null);
      })
      .catch((falha: unknown) => {
        if (atual) setErro(falha instanceof Error ? falha.message : "Não consegui ler o Analytics.");
      })
      .finally(() => {
        if (atual) setCarregando(false);
      });
    return () => {
      atual = false;
    };
  }, [visao]);

  const respondidos = estado?.cozinha.resumo.respondidos ?? 0;
  const disponiveis = useMemo(() => new Set(dados?.escopos_disponiveis.map((escopo) => escopo.escopo)), [dados]);
  const categorias = useMemo(() => [...new Set((dados?.ranking ?? []).map((prato) => prato.categoria))], [dados]);
  const cenarios = useMemo<Cenario[]>(() => dados?.ranking.find((prato) => prato.preco)?.preco?.cenarios ?? [], [dados]);
  const taxa = dados?.ranking.find((prato) => prato.preco)?.preco?.taxa ?? TAXA_PADRAO;
  const lista = (dados?.ranking ?? []).filter(
    (prato) => (!categoria || prato.categoria === categoria) && (!soDaPraFazer || prato.cozinha?.status !== "nao_da"),
  );
  const onde = dados?.escopos_disponiveis.find((escopo) => escopo.escopo === visao)?.onde;
  const preenchida = respondidos > 0;

  const escolher = (proxima: Visao) => {
    setVisao(proxima);
    setCategoria(null);
  };

  return (
    <Sheet titleId="analytics-titulo" title="Analytics" icon={<ChartIcon />} onClose={onClose} wide>
      <p className="sheet-note">Os pratos que mais vendem, com o preço médio e quanto cobrar para lucrar, priorizados pelo lugar da sua loja: zona, cidade, estado, região e país.</p>
      <p className="nota-exemplo">{dados?.aviso ?? "Base de exemplo criada para a demonstração do desafio: dados ilustrativos, não são vendas reais."}</p>

      <QuantoCobrarCard cenarios={cenarios} margem={margem} onMargem={setMargem} taxa={taxa} />

      {dados ? (
        dados.local_definido ? (
          <div className="local-card">
            <StoreIcon />
            <div>
              <strong>Sua loja</strong>
              <small>{textoDoLocal(dados.local)}</small>
            </div>
            <button type="button" className="link-btn" onClick={onNegocio}>
              Alterar
            </button>
          </div>
        ) : (
          <div className="local-card falta">
            <StoreIcon />
            <div>
              <strong>A loja ainda não tem localização</strong>
              <small>Informe estado, cidade e zona em Meu negócio para priorizar pelo lugar. Por enquanto vale só o ranking do país.</small>
            </div>
            <button type="button" className="link-btn" onClick={onNegocio}>
              Informar
            </button>
          </div>
        )
      ) : null}

      {estado ? (
        <div className={preenchida ? "local-card" : "local-card falta"}>
          <KitchenIcon />
          <div>
            <strong>{preenchida ? "Sua cozinha" : "A sua cozinha ainda não está preenchida"}</strong>
            <small>
              {preenchida
                ? `${respondidos} de ${estado.cozinha.resumo.total} itens respondidos. Cada prato mostra se a sua cozinha dá conta.`
                : "Marque o que você tem e sabe fazer em Minha cozinha para ver quais pratos você consegue fazer."}
            </small>
          </div>
          <button type="button" className="link-btn" onClick={onCozinha}>
            {preenchida ? "Alterar" : "Preencher"}
          </button>
        </div>
      ) : null}

      <div className="escopos" role="tablist" aria-label="Nível do ranking">
        {ABAS.map((aba) => {
          const semDados = aba.id !== "prioridade" && dados !== null && !disponiveis.has(aba.id);
          return (
            <button
              key={aba.id}
              type="button"
              role="tab"
              aria-selected={visao === aba.id}
              className={visao === aba.id ? "escopo on" : "escopo"}
              disabled={semDados}
              title={semDados ? "A base não tem dados deste nível para o lugar da loja" : undefined}
              onClick={() => escolher(aba.id)}
            >
              {aba.rotulo}
            </button>
          );
        })}
      </div>
      <p className="escopo-onde">
        {visao === "prioridade"
          ? dados && !dados.local_definido
            ? "Ranking do país: sem a localização, não há como priorizar pelo lugar da loja."
            : "A zona pesa mais que a cidade, que pesa mais que o estado, a região e o país. Quem lidera perto da loja sobe."
          : onde
            ? `${ROTULO[visao]}: ${onde}.`
            : ""}
      </p>
      {visao !== "prioridade" && dados?.leitura ? <p className="leitura">{dados.leitura}</p> : null}
      {dados?.mensagem && dados.local_definido ? <p className="sheet-note dica">{dados.mensagem}</p> : null}

      {categorias.length > 1 || preenchida ? (
        <div className="toggle-chips" role="group" aria-label="Filtrar">
          {categorias.length > 1 ? (
            <>
              <button type="button" className={categoria === null ? "toggle on" : "toggle"} aria-pressed={categoria === null} onClick={() => setCategoria(null)}>
                Todas
              </button>
              {categorias.map((nome) => (
                <button key={nome} type="button" className={categoria === nome ? "toggle on" : "toggle"} aria-pressed={categoria === nome} onClick={() => setCategoria(nome)}>
                  {nome}
                </button>
              ))}
            </>
          ) : null}
          {preenchida ? (
            <button type="button" className={soDaPraFazer ? "toggle on" : "toggle"} aria-pressed={soDaPraFazer} onClick={() => setSoDaPraFazer((v) => !v)}>
              Só o que a minha cozinha faz
            </button>
          ) : null}
        </div>
      ) : null}

      {erro ? (
        <p className="sheet-note error" role="alert">
          {erro}
        </p>
      ) : null}
      {carregando && !dados ? <p className="sheet-note">Carregando os mais vendidos…</p> : null}
      {dados && lista.length === 0 && !carregando ? <p className="sheet-note vazio">Nenhum prato neste filtro.</p> : null}

      <ol className="rank" aria-busy={carregando}>
        {lista.map((prato) => (
          <Posicao key={prato.prato} prato={prato} daLoja={dados?.local_definido ?? false} margem={margem} cozinhaPreenchida={preenchida} onAsk={onAsk} />
        ))}
      </ol>
      {dados ? <p className="sheet-note dica fonte">Fonte: {dados.fonte}</p> : null}
    </Sheet>
  );
}

/** A regra da taxa em linguagem de balcão, a margem que ela quer e uma calculadora com o custo real do prato dela. */
function QuantoCobrarCard({ cenarios, margem, onMargem, taxa }: { cenarios: Cenario[]; margem: number; onMargem: (indice: number) => void; taxa: number }) {
  const [custo, setCusto] = useState("");
  const [calculo, setCalculo] = useState<QuantoCobrar | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const recebe = 1 - taxa;
  const alvo = cenarios[margem] ?? cenarios[0];

  useEffect(() => {
    const valor = lerNumero(custo);
    if (!(valor > 0)) {
      setCalculo(null);
      setErro(custo.trim() ? "Informe o custo em R$, maior que zero." : null);
      return;
    }
    let atual = true;
    const id = window.setTimeout(() => {
      getPrecos(valor)
        .then((resposta) => {
          if (!atual) return;
          setCalculo(resposta);
          setErro(null);
        })
        .catch((falha: unknown) => {
          if (atual) setErro(falha instanceof Error ? falha.message : "Não consegui calcular.");
        });
    }, 300);
    return () => {
      atual = false;
      window.clearTimeout(id);
    };
  }, [custo]);

  return (
    <section className="cobrar-card" aria-labelledby="cobrar-titulo">
      <h3 id="cobrar-titulo">Quanto cobrar para lucrar depois da taxa</h3>
      <p className="cobrar-regra">
        O iFood fica com <b>{pct(taxa)}</b> de cada venda: de cada <b>R$ 100</b> que o cliente paga, você recebe <b>R$ {numero(recebe * 100)}</b>.
      </p>
      <dl className="cobrar-contas">
        <div>
          <dt>Para não ter prejuízo</dt>
          <dd>
            preço = custo ÷ {numero(recebe)}
          </dd>
        </div>
        {alvo ? (
          <div>
            <dt>Para sobrar {pct(alvo.margem)} do que você recebe</dt>
            <dd>
              preço = custo ÷ {numero(recebe * (1 - alvo.margem))}
            </dd>
          </div>
        ) : null}
      </dl>
      {cenarios.length > 0 ? (
        <div className="toggle-chips" role="group" aria-label="Quanto você quer que sobre de lucro">
          {cenarios.map((cenario, indice) => (
            <button key={cenario.nome} type="button" className={indice === margem ? "toggle on" : "toggle"} aria-pressed={indice === margem} onClick={() => onMargem(indice)}>
              {cenario.nome} · sobra {pct(cenario.margem)}
            </button>
          ))}
        </div>
      ) : null}

      <label className="field calc">
        <span>Quanto custa fazer 1 prato? (o CMV, em R$)</span>
        <input value={custo} inputMode="decimal" placeholder="ex.: 12,50" onChange={(event) => setCusto(event.target.value)} />
      </label>
      {erro ? (
        <p className="sheet-note error" role="alert">
          {erro}
        </p>
      ) : calculo ? (
        <ul className="cobrar-resultado" aria-live="polite">
          <li className="minimo">
            <span>Sem prejuízo, cobre pelo menos</span>
            <b>{brl(calculo.preco_minimo)}</b>
          </li>
          {calculo.cenarios.map((cenario, indice) => (
            <li key={cenario.nome} className={indice === margem ? "alvo" : undefined}>
              <span>
                {cenario.nome} (sobra {pct(cenario.margem)}): você recebe {brl(cenario.recebe)} e lucra {brl(cenario.lucro)}
              </span>
              <b>{brl(cenario.preco)}</b>
            </li>
          ))}
        </ul>
      ) : (
        <p className="sheet-note dica">Digite o custo do prato para ver quanto cobrar na hora. Abaixo, cada prato já mostra o preço com um custo típico de exemplo.</p>
      )}
    </section>
  );
}

function SeloCozinha({ prato, preenchida }: { prato: MaisVendido; preenchida: boolean }) {
  const c = prato.cozinha;
  if (!c) return null;
  if (c.status === "nao_da") return <span className="selo-cozinha nao_da">Falta {[...new Set(c.falta.map((f) => f.precisa))].join(" e ")}</span>;
  if (c.status === "atencao") return <span className="selo-cozinha atencao">Atenção: habilidade</span>;
  if (!preenchida) return null;
  if (c.status === "ok") return <span className="selo-cozinha ok">Dá para fazer</span>;
  return c.status === "confirmar" ? <span className="selo-cozinha confirmar">A confirmar</span> : null;
}

/** Quanto cobrar por este prato: o preço para a margem escolhida, o mínimo sem prejuízo e o que sobra se ela cobrar o preço do mercado. */
function Cobrar({ preco, margem }: { preco: QuantoCobrar; margem: number }) {
  const alvo = preco.cenarios[margem] ?? preco.cenarios[0];
  const mercado = preco.mercado;
  const recebe = 1 - preco.taxa;
  return (
    <div className="cobrar" role="group" aria-label="Quanto cobrar para lucrar">
      <p className="cobrar-titulo">Quanto cobrar</p>
      {alvo ? (
        <p className="cobrar-preco">
          <span>Cobre a partir de</span>
          <b>{brl(alvo.preco)}</b>
          <small>para sobrar {pct(alvo.margem)} do que você recebe</small>
        </p>
      ) : null}
      <p className="cobrar-linha">
        Sem prejuízo: <b>{brl(preco.preco_minimo)}</b> <small>(custo {brl(preco.cmv)} ÷ {numero(recebe)})</small>
      </p>
      {mercado ? (
        <p className={mercado.viavel ? "cobrar-mercado" : "cobrar-mercado ruim"}>
          No preço do mercado ({brl(mercado.preco)}): o iFood fica com {brl(mercado.taxa_plataforma)}, você recebe {brl(mercado.recebe_liquido)} e{" "}
          {mercado.viavel ? <b>lucra {brl(mercado.lucro)}</b> : <b>perde {brl(-mercado.lucro)}</b>} por prato.
        </p>
      ) : null}
      <small className="cobrar-aviso">Custo típico de exemplo. Com o seu custo real o número muda: use a calculadora acima ou peça à consultora.</small>
    </div>
  );
}

function Posicao({
  prato,
  daLoja,
  margem,
  cozinhaPreenchida,
  onAsk,
}: {
  prato: MaisVendido;
  daLoja: boolean;
  margem: number;
  cozinhaPreenchida: boolean;
  onAsk: (nome: string) => void;
}) {
  const nota = prato.prioridade ?? prato.indice ?? 0;
  const rotuloNota = prato.prioridade === undefined ? "Índice de vendas" : daLoja ? "Prioridade para a sua loja" : "Índice de vendas no país";
  const status = prato.cozinha?.status;
  return (
    <li className="rank-item">
      <span className="rank-pos">{prato.posicao}</span>
      <div className="rank-main">
        <div className="rank-title">
          <strong>{prato.prato}</strong>
          <span className="tag">{prato.categoria}</span>
          <SeloCozinha prato={prato} preenchida={cozinhaPreenchida} />
        </div>
        <p className="rank-preco">
          <b>{brl(prato.preco_medio)}</b> <small>preço médio · faixa {prato.faixa}</small>
        </p>
        {status === "nao_da" || status === "atencao" ? <p className={`rank-aviso ${status}`}>{prato.cozinha?.mensagens[0]}</p> : null}
        {prato.preco ? <Cobrar preco={prato.preco} margem={margem} /> : null}
        <div className="rank-barra" role="img" aria-label={`${rotuloNota} ${nota} de 100`}>
          <span style={{ width: `${Math.min(100, Math.max(0, nota))}%` }} />
        </div>
        <small className="rank-meta">
          {rotuloNota}: {nota}
        </small>
        <small className="rank-ing">Ingredientes típicos: {prato.ingredientes}</small>
        {prato.cozinha?.observacao ? <small className="rank-ing">Dica: {prato.cozinha.observacao}</small> : null}
        {prato.onde && prato.onde.length > 0 ? (
          <div className="rank-onde" aria-label="Posição em cada nível">
            {prato.onde.map((nivel) => (
              <span key={nivel.escopo}>
                {ROTULO[nivel.escopo]} #{nivel.posicao}
              </span>
            ))}
          </div>
        ) : null}
        <button type="button" className="mini-btn forte" onClick={() => onAsk(prato.prato)}>
          <SparkIcon />
          Montar com a consultora
        </button>
      </div>
    </li>
  );
}
