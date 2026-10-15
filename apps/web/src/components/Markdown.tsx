import { renderMarkdown } from "../lib/markdown";
export function Markdown({ source, className = "" }: { source: string; className?: string }) { return <article className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: renderMarkdown(source) }} />; }
