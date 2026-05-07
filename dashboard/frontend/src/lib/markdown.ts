/**
 * Markdown → HTML renderer for narrative artifact ``document`` fields.
 *
 * Pipeline (per Stage 2 of `docs/hammock-workflow.md`):
 *   remark-parse  → parse markdown
 *   remark-gfm    → tables, task lists, autolinks
 *   remark-rehype → markdown AST → HTML AST
 *   rehype-sanitize → strip unsafe HTML (defence-in-depth; the agent
 *     authoring the document is trusted, but the dashboard is the
 *     ultimate consumer and must not render arbitrary script tags)
 *   rehype-highlight → syntax highlighting on code blocks
 *   rehype-stringify → HTML AST → string
 *
 * The full deps were already installed in package.json; this module is
 * the first user.
 */

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeSanitize from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import rehypeStringify from "rehype-stringify";

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype, { allowDangerousHtml: false })
  .use(rehypeSanitize)
  .use(rehypeHighlight, { detect: true, ignoreMissing: true })
  .use(rehypeStringify);

export async function renderMarkdown(source: string): Promise<string> {
  const file = await processor.process(source);
  return String(file);
}
