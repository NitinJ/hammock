/**
 * Stage 2 — `EnvelopeView` renders the `document` field as markdown.
 *
 * Contract per `docs/hammock-workflow.md`:
 * - When ``envelope.value.document`` is a non-empty string, render it as
 *   the primary view (markdown → HTML).
 * - All other typed fields render in a collapsible metadata panel.
 * - When ``document`` is absent, fall back to the existing JSON-dump
 *   view for backward compatibility with non-narrative envelopes.
 */
import { mount, flushPromises } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import EnvelopeView from "@/components/jobs/EnvelopeView.vue";
import type { EnvelopePayload } from "@/api/schema.d";

function envWithDocument(doc: string, extra: Record<string, unknown> = {}): EnvelopePayload {
  return {
    type: "design-spec",
    version: "1",
    repo: null,
    producer_node: "write-design-spec",
    produced_at: "2026-05-07T10:00:00Z",
    value: {
      title: "Refactor X",
      overview: "Two sentences.",
      document: doc,
      ...extra,
    },
  };
}

function envWithoutDocument(): EnvelopePayload {
  return {
    type: "pr",
    version: "1",
    repo: "me/repo",
    producer_node: "implement",
    produced_at: "2026-05-07T10:00:00Z",
    value: {
      url: "https://github.com/me/repo/pull/1",
      number: 1,
    },
  };
}

describe("EnvelopeView", () => {
  it("renders document as markdown when present", async () => {
    const env = envWithDocument(
      "## Section heading\n\nA paragraph with **bold** text.\n\n- item one\n- item two",
    );
    const wrapper = mount(EnvelopeView, { props: { name: "design_spec", envelope: env } });
    await flushPromises();
    const html = wrapper.html();
    // Markdown rendered to HTML — heading, bold, list.
    expect(html).toContain("<h2");
    expect(html).toContain("Section heading");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<li>item one</li>");
  });

  it("falls back to JSON dump when document field is absent", () => {
    const env = envWithoutDocument();
    const wrapper = mount(EnvelopeView, { props: { name: "pr", envelope: env } });
    const text = wrapper.text();
    // Plain JSON visible.
    expect(text).toContain("https://github.com/me/repo/pull/1");
    // No rendered markdown elements.
    expect(wrapper.html()).not.toContain("<h2");
  });

  it("still shows other typed fields as a metadata panel when document is rendered", async () => {
    const env = envWithDocument("## Body\n\nBody text.\n");
    const wrapper = mount(EnvelopeView, { props: { name: "design_spec", envelope: env } });
    await flushPromises();
    const text = wrapper.text();
    // Title and overview (other typed fields) still surfaced somewhere.
    expect(text).toContain("Refactor X");
    expect(text).toContain("Two sentences.");
  });

  it("treats an empty document field as if absent (no broken markdown render)", () => {
    const env = envWithDocument("");
    const wrapper = mount(EnvelopeView, { props: { name: "design_spec", envelope: env } });
    // Empty document → fall back to JSON view; no <h2> or markdown HTML.
    expect(wrapper.html()).not.toContain("<h2");
  });
});
