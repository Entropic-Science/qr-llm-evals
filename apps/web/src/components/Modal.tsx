import { useEffect, useId, useRef, type PropsWithChildren, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalProps extends PropsWithChildren {
  open: boolean;
  title: string;
  eyebrow?: string;
  footer?: ReactNode;
  onClose: () => void;
  /** `page` is a dialog as tall as the window: its body scrolls and its header and footer stay in sight. */
  size?: "default" | "page";
  /** Drawn in the header under the title: a filter, a toggle. */
  toolbar?: ReactNode;
  /** Where the focus goes when the dialog closes, when that is not where it was when the dialog opened. */
  restoreFocus?: () => HTMLElement | null;
}

/** The dialogs that are open, the one on top last. Only that one answers Escape. */
const stack: symbol[] = [];

const TABBABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";

export function Modal({ open, title, eyebrow, footer, onClose, size = "default", toolbar, restoreFocus, children }: ModalProps) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  // The latest handlers are read when they are needed, so a caller that passes a new function on every render
  // does not make the dialog close and open again, which would take the focus away from what is being typed.
  const handlers = useRef({ onClose, restoreFocus });
  useEffect(() => { handlers.current = { onClose, restoreFocus }; });

  useEffect(() => {
    if (!open) return;
    const id = Symbol("modal");
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (stack[stack.length - 1] !== id) return;
      if (event.key === "Escape") { handlers.current.onClose(); return; }
      if (event.key !== "Tab") return;
      // The page behind is inert to a reader (aria-modal); the Tab key stays inside as well.
      const dialog = dialogRef.current;
      if (!dialog) return;
      // Only what is drawn can take the focus: not what a filter hid, nor what sits in a folded <details>.
      const tabbable = [...dialog.querySelectorAll<HTMLElement>(TABBABLE)].filter((element) => element.getClientRects().length > 0);
      const first = tabbable[0];
      const last = tabbable[tabbable.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      const outside = !(active instanceof Node) || !dialog.contains(active);
      if (event.shiftKey ? active === first || outside : active === last || outside) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    stack.push(id);
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("modal-open");
    closeRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      stack.splice(stack.indexOf(id), 1);
      if (stack.length === 0) document.body.classList.remove("modal-open");
      (handlers.current.restoreFocus?.() ?? previous)?.focus();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className={`modal-layer${size === "page" ? " modal-layer--page" : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className={`modal${size === "page" ? " modal--page" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal__header">
          <div>{eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}<h2 id={titleId}>{title}</h2></div>
          <button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="Close dialog">×</button>
          {toolbar ? <div className="modal__toolbar">{toolbar}</div> : null}
        </header>
        <div className="modal__body">{children}</div>
        {footer ? <footer className="modal__footer">{footer}</footer> : null}
      </section>
    </div>,
    document.body,
  );
}
