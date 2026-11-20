import { useEffect, useId, useState } from "react";
import { IngredientesFicha } from "./ingredientes-ficha";
import { getAnalises } from "./lib/api";
import { brl, quantidade } from "./lib/format";
import { DivulgacaoChat } from "./divulgacao";
import "./conducao-chat.css";
import { fichaPrecisaDaFonte, mensagemParaContinuarPrato, pendenciasDoPrato, situacaoDoPrato } from "./lib/pratos";
import { PEDIR_OPCOES_COM_A_IA, PEDIR_OUTRAS_OPCOES, PEDIR_PRATO_PROPRIO } from "./lib/prompts";
import type { OpcaoSugerida } from "./lib/sugestoes";
import type { AcaoJornada, AnalisePreco, Estado, MedidaPendente, PratoEscolhido } from "./types";

type Props = {
  estado: Estado | null;
  bloqueado: boolean;
  versao: string;
  /** Receitas que a consultora mostrou nesta conversa e ainda esperam a escolha dela. */
  sugestoes: OpcaoSugerida[];
  onEnviar: (visivel: string, paraAgente?: string, descartaSugestoes?: boolean) => void;
  onCadastro: (tipo: "negocio" | "cozinha") => void;
  onAcao: (acao: AcaoJornada, visivel: string, mensagem?: string, descartaSugestoes?: boolean) => Promise<string | null>;
  onAnalytics: () => void;
};

const PENDENCIAS_DE_MONTAGEM = new Set([
  "ingrediente_pendente",
  "tempo_da_receita_pendente",
  "preparo_adaptacao_pendente",
]);

function perguntaDaPendencia(prato: PratoEscolhido): NonNullable<NonNullable<Estado["jornada"]>["pergunta"]> | null {
  const bloqueio = prato.bloqueios?.[0];
  if (!bloqueio || (bloqueio.tipo !== "equipamento_nao_informado" && bloqueio.tipo !== "tecnica_nao_informada") || !bloqueio.detalhe.includes(":")) return null;
  // Estes tipos e o nome antes de ':' vêm da conferência da receita no backend.
  // Não inferimos gás, energia ou respostas sobre outros itens a partir deles.
  const item = bloqueio.detalhe.split(":", 1)[0].trim().replace(/_/g, " ");
  // "Forno ou air fryer" precisa de uma escolha específica, não de um sim
  // que possa virar evidência de posse dos dois equipamentos.
  if (!item || item.length > 80 || /\b(?:ou|e)\b/i.test(item.normalize("NFD").replace(/[\u0300-\u036f]/g, ""))) return null;
  return bloqueio.tipo === "equipamento_nao_informado"
    ? { texto: `Você tem ${item} funcionando para esta receita?`, opcoes: [
      { rotulo: `Tenho ${item}`, resposta: `Tenho ${item}.`, mensagem: `Tenho ${item}.` },
      { rotulo: `Não tenho ${item}`, resposta: `Não tenho ${item}.`, mensagem: `Não tenho ${item}.` },
    ] }
    : { texto: `Você domina a técnica de ${item} para esta receita?`, opcoes: [
      { rotulo: "Sei fazer", resposta: `Eu domino a técnica de ${item}.`, mensagem: `Eu domino a técnica de ${item}.` },
      { rotulo: "Ainda não sei", resposta: `Eu não domino a técnica de ${item}.`, mensagem: `Eu não domino a técnica de ${item}.` },
    ] };
}

function RespostaDaMedida({ pratoId, medida, onEnviar }: { pratoId: number; medida: MedidaPendente; onEnviar: Props["onEnviar"] }) {
  const [entrada, setEntrada] = useState("");
  const [unidade, setUnidade] = useState("g");
  const dicaId = useId();
  const valor = entrada.trim();
  const numero = Number(valor.replace(",", "."));
  const valida = /^\d+(?:[.,]\d+)?$/.test(valor) && Number.isFinite(numero) && numero > 0;
  const invalida = Boolean(valor) && !valida;

  return (
    <form className="conducao-medida" onSubmit={(event) => {
      event.preventDefault();
      if (!valida) return;
      const resposta = `Vou usar ${valor} ${unidade} de ${medida.rotulo} neste lote.`;
      onEnviar(resposta, `${resposta} Continue o prato #${pratoId}. Medidas ${medida.cotacao}.`);
    }}>
      <label className="field">
        <span>Quantidade no lote</span>
        <input type="text" inputMode="decimal" value={entrada} placeholder="Ex. 5" onChange={(event) => setEntrada(event.target.value)}
          aria-invalid={invalida} aria-describedby={invalida ? dicaId : undefined} />
      </label>
      <label className="field">
        <span>Unidade</span>
        <select value={unidade} onChange={(event) => setUnidade(event.target.value)}>
          <option value="g">Gramas (g)</option>
          <option value="kg">Quilos (kg)</option>
          <option value="ml">Mililitros (ml)</option>
          <option value="L">Litros (L)</option>
        </select>
      </label>
      <button type="submit" className="chip" disabled={!valida}>Confirmar medida</button>
      {invalida ? <small className="conducao-dica" id={dicaId}>Digite um número maior que zero e escolha a unidade ao lado.</small> : null}
    </form>
  );
}

/** São escolhas da pessoa sobre os dados atuais; a consultora executa e confirma no chat. */
export function ConducaoChat({ estado, bloqueado, versao, sugestoes, onEnviar, onCadastro, onAcao, onAnalytics }: Props) {
  const [analises, setAnalises] = useState<AnalisePreco[]>([]);
  const [pratoFocado, setPratoFocado] = useState<number | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const todosOsPratos = estado?.pratos_escolhidos ?? [];
  const idsAtivos = estado?.jornada?.pratos_ativos;
  const pratos = idsAtivos === undefined ? todosOsPratos : todosOsPratos.filter(({ id }) => idsAtivos.includes(id));
  const pendentes = pratos.filter((prato) => situacaoDoPrato(prato).etapa !== "aprovado");
  const precosPendentes = pendentes.filter((prato) => situacaoDoPrato(prato).etapa === "preco");
  const passo = estado?.jornada?.passo ?? (estado?.cadastro?.negocio_pendente ? "negocio" : precosPendentes.length || pratos.length && !pendentes.length ? "divulgacao" : "montagem");
  const chavePrecos = passo === "divulgacao" ? precosPendentes.map(({ id }) => id).join(",") : "";
  const pendentesDaEtapa = passo === "montagem" ? pendentes.filter((prato) => situacaoDoPrato(prato).etapa !== "preco") : pendentes;
  const foco = estado?.jornada?.controle?.proprio_pendente ? undefined : pendentesDaEtapa.find(({ id }) => id === pratoFocado)
    ?? pendentesDaEtapa.find(({ id }) => id === estado?.jornada?.prato_id) ?? pendentesDaEtapa[0];

  useEffect(() => {
    let atual = true;
    // Nunca geramos nem gravamos análises ao abrir o chat. Somente lemos cenários já propostos.
    setAnalises([]);
    if (!chavePrecos || bloqueado) return;
    void getAnalises().then((resposta) => {
      if (atual) setAnalises(resposta.analises.filter((analise) => analise.status === "proposta"));
    }).catch(() => { /* A conversa continua disponível mesmo sem este atalho. */ });
    return () => { atual = false; };
  }, [chavePrecos, bloqueado, versao, estado]);

  if (!estado || bloqueado) return null;
  async function escolher(acao: AcaoJornada, visivel: string, mensagem?: string, descartaSugestoes = false) {
    if (ocupado) return;
    setOcupado(true);
    setErro(null);
    const falha = await onAcao(acao, visivel, mensagem, descartaSugestoes);
    setErro(falha);
    setOcupado(false);
  }

  const jornada = estado.jornada;
  const botoes = (opcoes: { rotulo: string; resposta?: string; mensagem: string; acao?: AcaoJornada; descartaSugestoes?: boolean }[]) =>
    <div className="chips">{opcoes.map((opcao) => <button type="button" className="chip" key={opcao.rotulo} disabled={ocupado}
      onClick={() => opcao.acao ? void escolher(opcao.acao, opcao.resposta ?? opcao.rotulo, opcao.mensagem, opcao.descartaSugestoes)
        : onEnviar(opcao.resposta ?? opcao.rotulo, opcao.mensagem, opcao.descartaSugestoes)}>{opcao.rotulo}</button>)}</div>;
  const falha = erro ? <p className="sheet-note error" role="alert">{erro}</p> : null;

  if (jornada?.controle?.pausado || jornada?.etapa === "pausa") return (
    <section className="conducao-chat" aria-label="Jornada em pausa">
      <p>Sem pressa. Seus dados estão salvos. Quando estiver pronta, continuamos daqui.</p>
      {botoes([{ rotulo: "Estou pronta, vamos continuar", acao: "confirmar_estoque", mensagem: "Conferi meu estoque e estou pronta. Apresente somente a escolha de ajuda para montar o prato.", resposta: "Sim, conferi meu estoque. Estou pronta para começar." }])}{falha}
    </section>
  );

  const perguntaNegocio = !estado.negocio?.tipo ? "Que tipo de negócio você tem?"
    : !estado.negocio.alimentacao.length ? "Que tipo de comida você quer vender?"
    : !estado.negocio.estado || !estado.negocio.cidade ? "Em qual cidade e estado fica seu negócio?"
    : "Qual é seu principal objetivo com o cardápio?";

  if (passo === "negocio") return (
    <section className="conducao-chat" aria-label="Conhecer seu negócio">
      <p><strong>{jornada?.pergunta?.texto || perguntaNegocio}</strong></p>
      <p>Vamos conhecer seu estilo de comida e seu objetivo. Os R$ 80,00 ficam para os complementos do cardápio.</p>
      {jornada?.pergunta?.opcoes.length ? botoes(jornada.pergunta.opcoes) : <button type="button" className="chip" onClick={() => onCadastro("negocio")}>Iniciar</button>}
      <p className="conducao-dica">Se preferir, conte na mensagem.</p>{falha}
    </section>
  );

  if (passo === "estoque") {
    const itens = jornada?.estoque ?? [];
    const lista = (linhas: typeof itens) => <ul className="conducao-estoque-lista">{linhas.map((item) => <li key={item.nome}>
      <div><strong>{item.nome}</strong><span>{quantidade(item.disponivel ?? item.quantidade, item.unidade)} disponíveis{item.reservado > 0 ? ` · ${quantidade(item.reservado, item.unidade)} reservados` : ""}</span></div>
      <span>{item.custo_unitario === null ? "Custo a confirmar" : `${brl(item.custo_unitario)}/${item.unidade}`}</span>
    </li>)}</ul>;
    return <section className="conducao-chat" aria-label="Seu estoque disponível">
      <p><strong>Este é o estoque que você já tem.</strong></p>
      <p>Os ingredientes que você usar entram nas despesas dos lotes. Você tem {brl(estado.orcamento.saldo)} disponíveis para novas compras.</p>
      {itens.length ? <>{lista(itens.slice(0, 5))}{itens.length > 5 ? <details className="conducao-estoque-completo"><summary>Ver os {itens.length} ingredientes e custos</summary>{lista(itens.slice(5))}</details> : null}</>
        : <p>Não consegui carregar a lista do estoque. Atualize a página para conferir antes de continuar.</p>}
      <div className="conducao-pergunta"><p><strong>{jornada?.pergunta?.texto || "Você está pronta para começar a montar seu prato?"}</strong></p>
        {botoes(jornada?.pergunta?.opcoes.length ? jornada.pergunta.opcoes : [
          { rotulo: "Sim, vamos começar", acao: "confirmar_estoque", resposta: "Sim, conferi meu estoque. Vamos começar.", mensagem: "Conferi meu estoque e estou pronta. Apresente somente a escolha de ajuda para montar o prato." },
          { rotulo: "Agora não", acao: "pausar", resposta: "Agora não, quero pausar.", mensagem: "" },
        ])}
      </div>{falha}
    </section>;
  }

  if (passo === "ajuda") return (
    <section className="conducao-chat" aria-label="Escolher como montar o prato">
      <p><strong>{jornada?.pergunta?.texto || "Você quer minha ajuda para montar seu prato?"}</strong></p>
      <p>Posso pesquisar receitas reais com seu estoque e o perfil da sua região. Você também pode trazer uma ideia de prato ou escolher um ingrediente.</p>
      {botoes(jornada?.pergunta?.opcoes.length ? jornada.pergunta.opcoes : [
        { rotulo: "Quero ajuda da IA", acao: "ajuda_ia", resposta: "Quero ajuda da IA para montar meu prato.", mensagem: "Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha." },
        { rotulo: "Quero montar meu prato", acao: "prato_proprio", resposta: "Quero montar meu próprio prato.", mensagem: "Quero montar meu próprio prato. Pergunte qual prato ou ingrediente tenho em mente, antes de pesquisar ou criar uma ficha." },
      ])}{falha}
    </section>
  );

  if (!foco && passo === "divulgacao") return <>
    <DivulgacaoChat estado={estado} bloqueado={ocupado}
      onEscolha={(acao, visivel, mensagem) => void escolher(acao, visivel, mensagem)} onEnviar={onEnviar} onAnalytics={onAnalytics} />
    {falha}
  </>;

  // A consultora já mostrou receitas e nada fica gravado antes da escolha. Sem estes botões a tela voltaria a
  // oferecer "ver sugestões", cada clique faria outra busca e a pessoa nunca chegaria a escolher um prato.
  if (!foco && sugestoes.length) {
    const unica = sugestoes.length === 1;
    const recusa = unica ? "desse prato" : "desses pratos";
    return (
      <section className="conducao-chat conducao-sugestoes" aria-label="Escolher entre as sugestões">
        <p><strong>Qual opção você quer escolher?</strong></p>
        {botoes([
          ...sugestoes.map(({ numero, nome }) => {
            const homonima = sugestoes.some((outra) => outra.numero !== numero && outra.nome === nome);
            return {
              rotulo: unica ? "Gostei do prato, vamos usá-lo" : `Gostei de «${nome}»${homonima ? ` (opção ${numero})` : ""}, vamos usá-lo`,
              resposta: `Gostei de «${nome}», vamos usá-lo.`,
              // O número identifica a versão que ela pediu para preparar depois da conferência.
              mensagem: `Quero usar a opção ${numero}.`,
            };
          }),
          { rotulo: `Não gostei ${recusa}, iniciar nova busca`, resposta: `Não gostei ${recusa}. Quero iniciar uma nova busca.`,
            mensagem: PEDIR_OUTRAS_OPCOES, descartaSugestoes: true },
          { rotulo: `Não gostei ${recusa}, quero fazer meu próprio prato`, acao: "prato_proprio",
            resposta: `Não gostei ${recusa}. Quero fazer meu próprio prato.`, mensagem: PEDIR_PRATO_PROPRIO, descartaSugestoes: true },
        ])}
        <p className="conducao-dica">{unica ? "Se preferir, responda na mensagem."
          : "Para ficar com mais de uma, escreva na mensagem: escolho as opções 1 e 2."}</p>{falha}
      </section>
    );
  }

  if (!foco) return (
    <section className="conducao-chat" aria-label="Montar seu primeiro prato">
      <p><strong>{jornada?.controle?.ajuda === "proprio" || jornada?.etapa === "proprio" ? "Qual prato ou ingrediente você tem em mente?" : jornada?.titulo || "Vamos montar seu prato"}</strong></p>
      <p>{jornada?.controle?.ajuda === "proprio" || jornada?.etapa === "proprio"
        ? "Conte sua ideia na mensagem. Vou conferir os ingredientes e só perguntar o que faltar para essa receita."
        : jornada?.descricao || "Vamos usar seu estoque e conferir a cozinha para a receita escolhida."}</p>
      {jornada?.controle?.ajuda === "ia" ? botoes([
        { rotulo: "Ver sugestões com a IA", resposta: "Quero ver sugestões de pratos com a IA.", mensagem: PEDIR_OPCOES_COM_A_IA },
        { rotulo: "Quero montar meu prato", acao: "prato_proprio", resposta: "Quero montar meu próprio prato.", mensagem: "Prefiro montar meu próprio prato. Pergunte qual prato ou ingrediente tenho em mente, antes de criar uma ficha." },
      ]) : null}{falha}
    </section>
  );

  return (
    <section className="conducao-chat" aria-label={passo === "divulgacao" ? "Escolher o preço do prato" : "Montar seu prato"}>
      {falha}
      {pendentesDaEtapa.length > 1 ? <label className="field conducao-selecao">
        <span>Qual prato vamos continuar?</span>
        <select value={foco?.id} onChange={(event) => setPratoFocado(Number(event.target.value))}>
          {pendentesDaEtapa.map((prato) => <option key={prato.id} value={prato.id}>{prato.nome} · {situacaoDoPrato(prato).titulo}</option>)}
        </select>
      </label> : null}
      {foco ? [foco].map((prato) => {
        const situacao = situacaoDoPrato(prato);
        const analise = analises.find(({ prato_id }) => prato_id === prato.id);
        const jornadaDoPrato = estado.jornada?.prato_id === prato.id ? estado.jornada : undefined;
        const perguntaDaJornada = jornadaDoPrato?.pergunta;
        const pergunta = perguntaDaJornada ?? (jornadaDoPrato?.pergunta_esgotada ? null : perguntaDaPendencia(prato));
        const conferirFonte = fichaPrecisaDaFonte(prato);
        const podeMontarComEstoque = prato.status === "selecionado" && prato.receita_id !== null && (
          conferirFonte || (prato.bloqueios?.some(({ tipo }) => PENDENCIAS_DE_MONTAGEM.has(tipo)) ?? false)
          || prato.veredito === "inviavel_por_enquanto"
        );
        const podeGuardarParaDepois = prato.status === "selecionado" && Boolean(pergunta || pendenciasDoPrato(prato).length);
        return (
          <div className="conducao-prato" key={prato.id}>
            <div className="conducao-prato-titulo"><strong>{prato.nome}</strong><span className={`selo-cozinha ${situacao.tom}`}>{situacao.titulo}</span></div>
            <p>{situacao.etapa === "pendente" && !pergunta && !conferirFonte ? pendenciasDoPrato(prato)[0] || situacao.descricao : situacao.descricao}</p>
            <IngredientesFicha ingredientes={prato.ingredientes_da_ficha} />
            {prato.ingredientes_faltantes?.length ? <div className="sheet-note" aria-label="Observações de ingredientes">
              <strong>Observações da ficha</strong>
              <ul>{prato.ingredientes_faltantes.map((nota) => <li key={nota.ingrediente}>{nota.observacao}</li>)}</ul>
            </div> : null}
            {prato.medidas_de_uso_estimadas?.length ? <div className="sheet-note" aria-label="Quantidades estimadas para o lote">
              <strong>Quantidades estimadas para o lote</strong>
              <ul>{prato.medidas_de_uso_estimadas.map((medida) => (
                <li key={medida.ingrediente}>
                  {quantidade(medida.quantidade, medida.unidade)} de {medida.ingrediente}
                  {medida.base ? <small> · {medida.base}</small> : null}
                </li>
              ))}</ul>
            </div> : null}
            {situacao.etapa === "revisao" && pendenciasDoPrato(prato)[0] ? <p><strong>O que mudou:</strong> {pendenciasDoPrato(prato)[0]}</p> : null}
            {podeMontarComEstoque || podeGuardarParaDepois ? <div className="chips">
              {podeMontarComEstoque ? <button type="button" className="chip" disabled={ocupado} onClick={() => {
                const mensagem = `Monte a ficha do prato #${prato.id} com o estoque disponível.`;
                onEnviar(mensagem);
              }}>Montar com meu estoque</button> : null}
              {podeGuardarParaDepois ? <button type="button" className="chip" disabled={ocupado} onClick={() => {
                onEnviar(`Quero guardar «${prato.nome}» para depois.`, `Deixe o prato #${prato.id} como rascunho para depois.`);
              }}>Guardar para depois</button> : null}
            </div> : null}
            {pergunta ? <div className="conducao-pergunta" role="group" aria-label={pergunta.texto}>
              <p className="conducao-pergunta-texto"><strong>{pergunta.texto}</strong></p>
              {pergunta.opcoes.length ? <div className="chips">{pergunta.opcoes.map((opcao) => <button type="button" className="chip" key={opcao.rotulo}
                onClick={() => onEnviar(opcao.resposta ?? opcao.rotulo,
                  perguntaDaJornada ? opcao.mensagem : `${opcao.mensagem} Continue o prato #${prato.id}.`)}>{opcao.rotulo}</button>)}</div> : null}
              {pergunta.medida ? <RespostaDaMedida key={pergunta.medida.cotacao} pratoId={prato.id} medida={pergunta.medida} onEnviar={onEnviar} />
                : <p className="conducao-dica">{pergunta.opcoes.length ? "Responda pelos botões ou escreva os detalhes na mensagem." : "Escreva suas respostas em uma mensagem para continuar."}</p>}
            </div> : null}
            {analise && situacao.etapa === "preco" && passo === "divulgacao" ? (
              <>
                <p>Escolha quanto cobrar por porção. A taxa é de 10% e o custo por porção é {brl(analise.cmv_por_porcao)}.</p>
                <div className="conducao-precos" role="group" aria-label={`Escolher preço para ${prato.nome}`}>
                  {analise.cenarios.map((cenario) => (
                    <button type="button" key={cenario.nome} className="conducao-preco" onClick={() => {
                      const valor = brl(cenario.preco_sugerido).replace(/\s+/g, " ");
                      onEnviar(`Quero vender «${prato.nome}» por ${valor} por porção. Registre no cardápio.`,
                        `Escolho vender o prato #${prato.id} por ${valor} por porção. Registre no cardápio. Cotação ${analise.cotacao}.`);
                    }}>
                      <span>{cenario.nome}</span>
                      <strong>{brl(cenario.preco_sugerido)}</strong>
                      <small>Recebe {brl(cenario.com_preco_sugerido.recebe_liquido)} · lucro {brl(cenario.com_preco_sugerido.lucro)}</small>
                    </button>
                  ))}
                </div>
                <p className="conducao-dica">Ao escolher, a consultora confere os dados e registra esse preço. Você também pode escrever outro valor.</p>
              </>
            ) : !pergunta ? (
              <div className="chips">
                <button type="button" className="chip" disabled={ocupado} onClick={() => {
                  const mensagem = mensagemParaContinuarPrato(prato);
                  onEnviar(mensagem.visivel, mensagem.paraAgente);
                }}>
                  {situacao.etapa === "preco" ? "Comparar preços no chat" : situacao.etapa === "aceite" ? "Revisar ingredientes e preparo" : situacao.acao}
                </button>
                {situacao.etapa === "aceite" ? <button type="button" className="chip" onClick={() => onEnviar(
                  `Aceito preparar «${prato.nome}» nessa versão, sem impedimentos. Mostre os cenários para eu escolher o preço.`,
                  `Aceito preparar o prato #${prato.id} (${prato.nome}) nessa versão, sem impedimentos. Confira a viabilidade e mostre os cenários para eu escolher o preço.`)}>Usar este prato</button> : null}
                <button type="button" className="chip" disabled={ocupado} onClick={() => void escolher("prato_proprio", "Quero montar meu próprio prato.",
                  "Quero montar meu próprio prato. Preserve as fichas anteriores e pergunte qual prato ou ingrediente tenho em mente antes de criar a nova ficha.")}>Quero montar meu prato</button>
              </div>
            ) : null}
            {conferirFonte && !pergunta ? <p className="conducao-dica">A consultora confere o preparo primeiro e reúne as confirmações da cozinha que ainda faltarem.</p> : null}
          </div>
        );
      }) : null}
    </section>
  );
}
