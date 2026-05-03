/**
 * Frontend template registry — fetches and caches UI templates from
 * GET /api/hil/templates/{name}.
 *
 * Per design doc § Form pipeline and template registry.
 */

export interface ContextArtifact {
  path: string;
  render: "markdown" | "verdict" | "text";
  label: string;
}

export interface TemplateFields {
  title?: string;
  context_artifacts?: ContextArtifact[];
  submit_label?: string;
  extra_help?: string;
}

export interface UiTemplate {
  name: string;
  description: string | null;
  hil_kinds: string[] | null;
  instructions: string | null;
  fields: TemplateFields | null;
}

/**
 * Fetch a named template from the backend.
 *
 * @param name - Template name (e.g. "ask-default-form")
 * @param projectSlug - Optional project slug for per-project override resolution
 * @returns Resolved UiTemplate, or null if not found (404).
 */
export async function fetchTemplate(
  name: string,
  projectSlug?: string,
): Promise<UiTemplate | null> {
  throw new Error("not implemented");
}
