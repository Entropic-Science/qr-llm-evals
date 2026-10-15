import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";
const markdown = new MarkdownIt({ html: false, linkify: true, typographer: true });
const docModules = import.meta.glob<string>("../../../../docs/*.md", { eager: true, query: "?raw", import: "default" });
const docs = new Map(Object.entries(docModules).map(([path, source]) => [path.match(/\/([^/]+)\.md$/)?.[1] ?? path, source]));
export function renderMarkdown(source: string): string { return DOMPurify.sanitize(markdown.render(source), { USE_PROFILES: { html: true }, FORBID_TAGS: ["script", "style", "iframe", "object", "embed"] }); }
export function getDoc(slug: string): string | undefined { return docs.get(slug.replace(/^\/+|\/+$/g, "")); }
export function getDocSlugs(): readonly string[] { return [...docs.keys()].sort(); }
export function getMarkdownSection(source: string, heading: string): string {
  const lines = source.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim().toLocaleLowerCase() === `## ${heading}`.toLocaleLowerCase());
  if (start < 0) return "";
  const relativeEnd = lines.slice(start + 1).findIndex((line) => /^##\s+/.test(line));
  const end = relativeEnd < 0 ? lines.length : start + 1 + relativeEnd;
  return lines.slice(start + 1, end).join("\n").trim();
}
