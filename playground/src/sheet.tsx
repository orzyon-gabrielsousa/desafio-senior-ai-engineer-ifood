import { useEffect } from "react";
import type { ReactNode } from "react";
import { CloseIcon } from "./icons";

type SheetProps = {
  titleId: string;
  title: string;
  icon: ReactNode;
  onClose: () => void;
  wide?: boolean;
  children: ReactNode;
};

/** Painel que desliza pela direita (carrinho, perfil do negócio, Analytics). Esc e o fundo escuro fecham. */
export function Sheet({ titleId, title, icon, onClose, wide = false, children }: SheetProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-wrap">
      <button type="button" className="sheet-overlay" aria-label="Fechar" onClick={onClose} />
      <aside className={wide ? "sheet wide" : "sheet"} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="sheet-head">
          <span className="sheet-icon">{icon}</span>
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn" aria-label="Fechar" onClick={onClose}>
            <CloseIcon />
          </button>
        </header>
        <div className="sheet-body">{children}</div>
      </aside>
    </div>
  );
}
