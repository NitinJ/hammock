/**
 * AgentChatTail renders claude's stream-json transcript:
 *  - "no transcript" empty state when has_chat=false
 *  - assistant text → markdown
 *  - assistant tool_use → chip
 *  - user tool_result → collapsed details
 *  - result → footer line with turns + cost
 */
import { mount, flushPromises } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentChatTail from "@/components/jobs/AgentChatTail.vue";

interface ChatBody {
  turns: Record<string, unknown>[];
  attempt: number;
  has_chat: boolean;
}

function mountWithFetch(body: ChatBody) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return mount(AgentChatTail, {
    props: { jobSlug: "j", nodeId: "n", attempt: 1 },
    global: {
      plugins: [[VueQueryPlugin, { queryClient: qc }]],
    },
  });
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AgentChatTail", () => {
  it("renders 'no transcript' when has_chat=false", async () => {
    const wrapper = mountWithFetch({ turns: [], attempt: 1, has_chat: false });
    await flushPromises();
    expect(wrapper.find("[data-testid='agent-chat-empty']").exists()).toBe(true);
    expect(wrapper.text()).toContain("No chat transcript");
  });

  it("renders assistant text as markdown", async () => {
    const wrapper = mountWithFetch({
      turns: [
        {
          type: "assistant",
          message: {
            role: "assistant",
            content: [{ type: "text", text: "## Hello\n\nA paragraph." }],
          },
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    // Markdown renderer is async; wait again for the watcher.
    await flushPromises();
    const html = wrapper.html();
    expect(html).toContain("<h2");
    expect(html).toContain("Hello");
  });

  it("renders a tool_use chip with name + summary", async () => {
    const wrapper = mountWithFetch({
      turns: [
        {
          type: "assistant",
          message: {
            role: "assistant",
            content: [
              {
                type: "tool_use",
                name: "Read",
                input: { file_path: "/etc/hosts" },
              },
            ],
          },
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    await flushPromises();
    const chip = wrapper.find("[data-testid='chat-block-tool-use']");
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toContain("Read");
    expect(chip.text()).toContain("file_path");
    expect(chip.text()).toContain("/etc/hosts");
  });

  it("renders tool_result inside a collapsed details element", async () => {
    const wrapper = mountWithFetch({
      turns: [
        {
          type: "user",
          message: {
            role: "user",
            content: [
              {
                type: "tool_result",
                content: "line1\nline2\nline3",
              },
            ],
          },
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    await flushPromises();
    const details = wrapper.find("[data-testid='chat-block-tool-result']");
    expect(details.exists()).toBe(true);
    // Closed by default.
    expect((details.element as HTMLDetailsElement).open).toBe(false);
    expect(details.find("summary").text()).toContain("tool result");
    expect(details.find("summary").text()).toContain("17"); // length of content
  });

  it("renders result footer with turns and cost", async () => {
    const wrapper = mountWithFetch({
      turns: [
        {
          type: "result",
          is_error: false,
          num_turns: 4,
          total_cost_usd: 0.0123,
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    await flushPromises();
    const footer = wrapper.find("[data-testid='chat-turn-result']");
    expect(footer.exists()).toBe(true);
    expect(footer.text()).toContain("Done");
    expect(footer.text()).toContain("4 turns");
    expect(footer.text()).toContain("$0.0123");
  });
});
