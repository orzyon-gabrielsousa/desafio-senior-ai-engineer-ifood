import { useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Avatar } from "./brand";
import type { Acao, Estado, Negocio, Nivel, NovaCozinha, ValorRotina } from "./types";
import "./cadastro-chat.css";

export type TipoCadastro = "negocio" | "cozinha";

const ETAPAS_COZINHA = ["Equipamentos", "Utensílios", "Técnicas", "Rotina"];
const ETAPAS_NEGOCIO = ["Atividade", "Comida", "Localização", "Objetivo"];
const NEGOCIO_VAZIO: Negocio = { tipo: "", alimentacao: [], pais: "Brasil", estado: "", cidade: "", zona: "", objetivo: "" };
const ROTULO_NIVEL: Record<Nivel, string> = { sim: "Sei fazer", aprendendo: "Aprendendo", nao: "Não sei" };

export function cadastroPedido(texto: string): TipoCadastro | null {
  const pedidos = [...texto.matchAll(/\[\[cadastro:(negocio|cozinha)\]\]/gi)];
  const ultimo = pedidos.at(-1)?.[1].toLowerCase();
  return ultimo === "negocio" || ultimo === "cozinha" ? ultimo : null;
}

export function esconderPedidoCadastro(texto: string): string {
  const limpo = texto.replace(/\s*\[\[cadastro:[a-z]+\]\]/gi, "");
  const inicio = limpo.lastIndexOf("[[");
  if (inicio >= 0) {
    const trecho = limpo.slice(inicio).toLowerCase();
    const marcador = "[[cadastro:";
    if (marcador.startsWith(trecho) || (trecho.startsWith(marcador) && !trecho.includes("\n"))) {
      return limpo.slice(0, inicio).trimEnd();
    }
  }
  return limpo.trimEnd();
}

type CadastroChatProps = {
  tipo: TipoCadastro;
  estado: Estado;
  bloqueado: boolean;
  onClose: () => void;
  onCozinha: (cozinha: NovaCozinha) => Acao;
  onNegocio: (campos: Partial<Negocio>) => Acao;
};

export function CadastroChat({ tipo, estado, bloqueado, onClose, onCozinha, onNegocio }: CadastroChatProps) {
  return (
    <article className="msg assistant" aria-label="Cadastro no chat">
      <Avatar size={34} />
      <div className="msg-col cadastro-col">
        {tipo === "cozinha"
          ? <CadastroCozinha estado={estado} bloqueado={bloqueado} onSave={onCozinha} onClose={onClose} />
          : <CadastroNegocio estado={estado} bloqueado={bloqueado} onSave={onNegocio} onClose={onClose} />}
      </div>
    </article>
  );
}

function Chip({ marcado, onClick, children, dica }: { marcado: boolean; onClick: () => void; children: ReactNode; dica?: string }) {
  return (
    <button type="button" className={marcado ? "toggle on" : "toggle"} aria-pressed={marcado} title={dica} onClick={onClick}>
      {children}
    </button>
  );
}

type FormularioProps = {
  titulo: string;
  etapas: string[];
  etapa: number;
  setEtapa: (etapa: number) => void;
  bloqueado: boolean;
  podeEnviar: boolean;
  onSave: () => Acao;
  onClose: () => void;
  children: ReactNode;
};

function Formulario({ titulo, etapas, etapa, setEtapa, bloqueado, podeEnviar, onSave, onClose, children }: FormularioProps) {
  const id = useId();
  const tituloRef = useRef<HTMLHeadingElement>(null);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const ultima = etapa === etapas.length - 1;
  const ocupado = bloqueado || salvando;

  function irPara(proxima: number) {
    setEtapa(proxima);
    tituloRef.current?.focus();
  }

  async function enviar(event: FormEvent) {
    event.preventDefault();
    if (ocupado || !podeEnviar) return;
    setSalvando(true);
    setErro(null);
    const falha = await onSave();
    setErro(falha);
    setSalvando(false);
  }

  return (
    <form className="cadastro-chat bubble" aria-labelledby={id} onSubmit={(event) => void enviar(event)}>
      <header className="cadastro-cabecalho">
        <h2 id={id} ref={tituloRef} tabIndex={-1}>{titulo}</h2>
        <span>{etapa + 1} de {etapas.length}</span>
      </header>
      <fieldset className="cadastro-campos" disabled={ocupado}>
        <legend>{etapas[etapa]}</legend>
        {children}
      </fieldset>
      {erro ? <p className="cadastro-erro" role="alert">{erro}</p> : null}
      <footer className="cadastro-acoes">
        <button className="cadastro-secundario" type="button" disabled={salvando} onClick={onClose}>Agora não</button>
        {etapa > 0 ? (
          <button className="cadastro-secundario" type="button" disabled={salvando} onClick={() => irPara(etapa - 1)}>Voltar</button>
        ) : null}
        {ultima ? (
          <button className="primary" type="submit" disabled={ocupado || !podeEnviar}>
            {salvando ? "Salvando…" : "Enviar respostas"}
          </button>
        ) : null}
        {!ultima ? (
          <button className="primary" type="button" disabled={salvando} onClick={() => irPara(etapa + 1)}>Continuar</button>
        ) : null}
      </footer>
    </form>
  );
}

function CadastroCozinha({ estado, bloqueado, onSave, onClose }: {
  estado: Estado; bloqueado: boolean; onSave: (cozinha: NovaCozinha) => Acao; onClose: () => void;
}) {
  const pendencias = estado.cadastro?.cozinha_campos ?? [];
  const opcoes = estado.opcoes.cozinha;
  const grupos = [opcoes.equipamentos.map(({ id }) => id), opcoes.utensilios.map(({ id }) => id), ["tecnicas"], opcoes.rotina.map(({ id }) => id)];
  const [etapa, setEtapa] = useState(() => Math.max(0, grupos.findIndex((grupo) => grupo.some((id) => pendencias.includes(id)))));
  const [modoTem, setModoTem] = useState(true);
  const [modoNivel, setModoNivel] = useState<Nivel>("sim");
  const [rascunho, setRascunho] = useState<NovaCozinha>({});
  const salvo = estado.cozinha;
  const faixaDeTempo = salvo.outros.find(({ grupo, chave }) => grupo === "restricoes" && chave === "tempo_por_cozinhada_faixa");
  const equipamento = (id: string) => (id in (rascunho.equipamentos ?? {}) ? rascunho.equipamentos?.[id] : salvo.equipamentos[id]);
  const habilidade = (id: string) => (id in (rascunho.habilidades ?? {}) ? rascunho.habilidades?.[id] : salvo.habilidades[id]);
  const rotina = (id: string) => (id in (rascunho.rotina ?? {}) ? rascunho.rotina?.[id] : salvo.rotina[id]);
  const mudarEquipamento = (id: string, valor: boolean | number | null) =>
    setRascunho((atual) => ({ ...atual, equipamentos: { ...atual.equipamentos, [id]: valor } }));
  const mudarHabilidade = (id: string, valor: Nivel | null) =>
    setRascunho((atual) => ({ ...atual, habilidades: { ...atual.habilidades, [id]: valor } }));
  const mudarRotina = (id: string, valor: ValorRotina | null) =>
    setRascunho((atual) => ({ ...atual, rotina: { ...atual.rotina, [id]: valor } }));

  const envio: NovaCozinha = {};
  if (rascunho.equipamentos) envio.equipamentos = Object.fromEntries(Object.entries(rascunho.equipamentos)
    .filter(([id, valor]) => (valor ?? null) !== (salvo.equipamentos[id] ?? null)));
  if (rascunho.habilidades) envio.habilidades = Object.fromEntries(Object.entries(rascunho.habilidades)
    .filter(([id, valor]) => (valor ?? null) !== (salvo.habilidades[id] ?? null)));
  if (rascunho.rotina) envio.rotina = Object.fromEntries(Object.entries(rascunho.rotina)
    .filter(([id, valor]) => (valor ?? null) !== (salvo.rotina[id] ?? null)));
  const podeEnviar = Object.values(envio).some((parte) => Object.keys(parte).length > 0);

  const fogao = equipamento("fogao");
  return (
    <Formulario titulo="Vamos montar sua cozinha" etapas={ETAPAS_COZINHA} etapa={etapa} setEtapa={setEtapa}
      bloqueado={bloqueado} podeEnviar={podeEnviar} onSave={() => onSave(envio)} onClose={onClose}>
      {etapa <= 1 ? (
        <>
          <p className="cadastro-dica">Escolha uma resposta e marque os itens. Toque de novo para desmarcar.</p>
          <div className="cadastro-modos" role="group" aria-label="Resposta de equipamento">
            <Chip marcado={modoTem} onClick={() => setModoTem(true)}>Tenho</Chip>
            <Chip marcado={!modoTem} onClick={() => setModoTem(false)}>Não tenho</Chip>
          </div>
          <div className="toggle-chips" role="group" aria-label={ETAPAS_COZINHA[etapa]}>
            {[...(etapa === 0 ? opcoes.equipamentos : opcoes.utensilios)]
              .sort((a, b) => Number(pendencias.includes(b.id)) - Number(pendencias.includes(a.id))).map((opcao) => {
              const valor = equipamento(opcao.id);
              const tem = typeof valor === "number" ? true : valor;
              const marcado = tem === modoTem;
              return (
                <Chip key={opcao.id} marcado={marcado} dica={opcao.dica} onClick={() => mudarEquipamento(opcao.id, marcado ? null : modoTem)}>
                  {opcao.label}
                  {typeof tem === "boolean" ? <small>{tem ? "Tenho" : "Não tenho"}</small>
                    : pendencias.includes(opcao.id) ? <small>Essencial</small> : null}
                </Chip>
              );
            })}
          </div>
          {etapa === 0 && fogao ? (
            <label className="field cadastro-fogao">
              <span>Bocas do fogão</span>
              <select value={typeof fogao === "number" ? fogao : ""} onChange={(event) => mudarEquipamento("fogao", event.target.value ? Number(event.target.value) : true)}>
                <option value="">Informar depois</option>
                {typeof fogao === "number" && !opcoes.bocas.includes(fogao) ? <option value={fogao}>{fogao}</option> : null}
                {opcoes.bocas.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          ) : null}
        </>
      ) : etapa === 2 ? (
        <>
          <p className="cadastro-dica">Escolha um nível e marque as técnicas que combinam com ele.</p>
          <div className="cadastro-modos" role="group" aria-label="Nível da técnica">
            {opcoes.niveis.map((opcao) => <Chip key={opcao.id} marcado={modoNivel === opcao.id} onClick={() => setModoNivel(opcao.id as Nivel)}>{opcao.label}</Chip>)}
          </div>
          <div className="toggle-chips" role="group" aria-label="Técnicas">
            {opcoes.habilidades.map((opcao) => {
              const nivel = habilidade(opcao.id);
              return (
                <Chip key={opcao.id} marcado={nivel === modoNivel} dica={opcao.dica} onClick={() => mudarHabilidade(opcao.id, nivel === modoNivel ? null : modoNivel)}>
                  {opcao.label}
                  {nivel ? <small>{ROTULO_NIVEL[nivel]}</small> : null}
                </Chip>
              );
            })}
          </div>
        </>
      ) : (
        opcoes.rotina.map((campo) => (
          <div key={campo.id} className="cadastro-grupo" role="group" aria-label={campo.label}>
            <strong>{campo.label}</strong>
            {campo.id === "tempo_por_cozinhada_min" && !rotina(campo.id) && faixaDeTempo
              ? <p className="cadastro-dica">Informado no chat: {faixaDeTempo.texto}. Você pode manter essa resposta ou escolher outro tempo.</p> : null}
            <div className="toggle-chips">
              {campo.opcoes.map(({ valor, label }) => (
                <Chip key={valor} marcado={rotina(campo.id) === valor} onClick={() => mudarRotina(campo.id, rotina(campo.id) === valor ? null : valor)}>{label}</Chip>
              ))}
            </div>
          </div>
        ))
      )}
    </Formulario>
  );
}

function CadastroNegocio({ estado, bloqueado, onSave, onClose }: {
  estado: Estado; bloqueado: boolean; onSave: (campos: Partial<Negocio>) => Acao; onClose: () => void;
}) {
  const [etapa, setEtapa] = useState(() => !estado.negocio?.tipo ? 0 : !estado.negocio.alimentacao.length ? 1 : !estado.negocio.estado || !estado.negocio.cidade ? 2 : 3);
  const [mudancas, setMudancas] = useState<Partial<Negocio>>({});
  const form = { ...NEGOCIO_VAZIO, ...estado.negocio, ...mudancas };
  const mudar = (parte: Partial<Negocio>) => setMudancas((atual) => ({ ...atual, ...parte }));
  const opcoes = estado.opcoes;

  return (
    <Formulario titulo="Vamos conhecer seu negócio" etapas={ETAPAS_NEGOCIO} etapa={etapa} setEtapa={setEtapa}
      bloqueado={bloqueado} podeEnviar={Object.keys(mudancas).length > 0} onSave={() => onSave(mudancas)} onClose={onClose}>
      {etapa === 0 ? (
        <>
          <p className="cadastro-dica">Primeiro, que tipo de negócio você tem?</p>
          <label className="field">
            <span>Tipo de negócio</span>
            <select value={form.tipo} onChange={(event) => mudar({ tipo: event.target.value })}>
              <option value="">Escolha uma opção</option>
              {opcoes.tipos.map((opcao) => <option key={opcao.id} value={opcao.id}>{opcao.label}</option>)}
            </select>
          </label>
        </>
      ) : etapa === 1 ? (
          <div className="cadastro-grupo" role="group" aria-label="Tipo de alimentação">
            <strong>Que tipo de comida você serve?</strong>
            <div className="toggle-chips">
              {opcoes.alimentacao.map((opcao) => (
                <Chip key={opcao.id} marcado={form.alimentacao.includes(opcao.id)} onClick={() => mudar({
                  alimentacao: form.alimentacao.includes(opcao.id)
                    ? form.alimentacao.filter((id) => id !== opcao.id)
                    : [...form.alimentacao, opcao.id],
                })}>{opcao.label}</Chip>
              ))}
            </div>
          </div>
      ) : etapa === 2 ? (
        <div className="cadastro-local">
          <label className="field">
            <span>Estado</span>
            <select value={form.estado} onChange={(event) => mudar({ estado: event.target.value })}>
              <option value="">Informar depois</option>
              {opcoes.estados.map((opcao) => <option key={opcao.id} value={opcao.id}>{opcao.label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Cidade</span>
            <input value={form.cidade} maxLength={60} onChange={(event) => mudar({ cidade: event.target.value })} />
          </label>
          <label className="field">
            <span>Zona da cidade</span>
            <select value={form.zona} onChange={(event) => mudar({ zona: event.target.value })}>
              <option value="">Informar depois</option>
              {opcoes.zonas.map((opcao) => <option key={opcao.id} value={opcao.id}>{opcao.label}</option>)}
            </select>
          </label>
        </div>
      ) : (
        <>
          <label className="field">
            <span>O que você quer alcançar com seu cardápio?</span>
            <input value={form.objetivo ?? ""} maxLength={240} placeholder="Ex.: começar a vender marmitas com lucro" onChange={(event) => mudar({ objetivo: event.target.value })} />
          </label>
          <p className="cadastro-dica">Seu orçamento inicial para complementos é de R$ 80,00. Vou ajudar a aproveitar os ingredientes que você já tem. O objetivo é opcional e pode ser ajustado depois.</p>
        </>
      )}
    </Formulario>
  );
}
