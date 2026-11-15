import ifoodLogo from "./assets/ifood-logo.svg";

// Logo do iFood (wordmark + setinha): o IFood_logo.svg do Wikimedia Commons, sem alterações.
// A marca é do iFood; aqui ela só ambienta a demo do desafio técnico.
const LOGO_RATIO = 1004 / 530.58;

export function IfoodLogo({ height = 32 }: { height?: number }) {
  return (
    <img
      className="ifood-logo"
      src={ifoodLogo}
      alt="iFood"
      width={Math.round(height * LOGO_RATIO)}
      height={height}
    />
  );
}

// O "i" do wordmark em círculo vermelho. As curvas são as mesmas do SVG oficial
// (a haste e o pingo do "i"), centradas num círculo de 48 × 48.
export function IfoodMark({ size = 36 }: { size?: number }) {
  return (
    <svg className="ifood-mark" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <circle cx="24" cy="24" r="24" fill="#ea1d2c" />
      <g transform="translate(18.1 10.56) scale(0.0868)">
        <path
          transform="translate(4 -224.71)"
          d="M-4,529.31H72.41l42.46-211.2H38.45ZM42.66,292.92h76.73L132,229.73H55.3Z"
          fill="#fff"
        />
      </g>
    </svg>
  );
}

// Avatar da consultora no chat; o ponto mostra se o modelo local já respondeu ao health check.
export function Avatar({ size = 36, status }: { size?: number; status?: "on" | "wait" }) {
  return (
    <span className="avatar" style={{ width: size, height: size }}>
      <IfoodMark size={size} />
      {status ? <i className={`avatar-dot ${status}`} /> : null}
    </span>
  );
}

// Logo do Sabor da Maria (a panela da Dona Maria), o delivery que aparece no topo.
export function Mark({ size = 36 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <rect width="48" height="48" rx="14" fill="#f6efe4" />
      <path d="M13 23h22l-1.7 13.4A6.2 6.2 0 0 1 27.2 42h-6.4a6.2 6.2 0 0 1-6.1-5.6L13 23z" fill="#c45c26" />
      <path d="M11 19.5h26v4.2H11z" fill="#8f3d16" />
      <path
        d="M20 19.5c.4-4.2 2.7-7.4 4-10.5 1.5 3.1 3.8 6.3 4.2 10.5"
        fill="none"
        stroke="#3f6b4a"
        strokeWidth="2.1"
        strokeLinecap="round"
      />
      <circle cx="31.5" cy="14" r="2.1" fill="#3f6b4a" />
    </svg>
  );
}
