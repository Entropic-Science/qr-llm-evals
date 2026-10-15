import { getDoc, getMarkdownSection } from "../lib/markdown";
import { Markdown } from "./Markdown";
import { Modal } from "./Modal";
export const determinismPopupText = getMarkdownSection(getDoc("determinism") ?? "", "Popup text");
export function DeterminismBriefing({ open, onClose }: { open: boolean; onClose: () => void }) { return <Modal open={open} onClose={onClose} eyebrow="Before you select deterministic" title="What this guarantee means" footer={<button className="button button--primary" type="button" onClick={onClose}>I understand</button>}><Markdown source={determinismPopupText} className="prose--compact" /></Modal>; }
