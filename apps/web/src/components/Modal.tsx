import { useEffect, useId, useRef, type PropsWithChildren, type ReactNode } from "react";
import { createPortal } from "react-dom";
interface ModalProps extends PropsWithChildren { open: boolean; title: string; eyebrow?: string; footer?: ReactNode; onClose: () => void }
export function Modal({ open, title, eyebrow, footer, onClose, children }: ModalProps) {
  const titleId = useId(); const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (!open) return; const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null; const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; document.addEventListener("keydown", onKeyDown); document.body.classList.add("modal-open"); closeRef.current?.focus(); return () => { document.removeEventListener("keydown", onKeyDown); document.body.classList.remove("modal-open"); previous?.focus(); }; }, [onClose, open]);
  if (!open) return null;
  return createPortal(<div className="modal-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId}><header className="modal__header"><div>{eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}<h2 id={titleId}>{title}</h2></div><button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="Close dialog">×</button></header><div className="modal__body">{children}</div>{footer ? <footer className="modal__footer">{footer}</footer> : null}</section></div>, document.body);
}
