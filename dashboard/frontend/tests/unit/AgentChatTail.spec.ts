/**
 * AgentChatTail renders claude's stream-json transcript:
 *  - "no transcript" empty state when has_chat=false
 *  - assistant text → markdown
 *  - assistant tool_use → chip
 *  - user tool_result → collapsed details
 *  - result → footer line with turns + cost
 *  - scroll preservation when user has scrolled up (Stage D)
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

function mountWithFetch(body: ChatBody, qc?: QueryClient) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = qc ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return mount(AgentChatTail, {
    props: { jobSlug: "j", nodeId: "n", attempt: 1 },
    global: {
      plugins: [[VueQueryPlugin, { queryClient: client }]],
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

  it("auto-scrolls to bottom when user is already at the bottom", async () => {
    const initialTurns = [
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "First" }] },
      },
    ];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = mountWithFetch({ turns: initialTurns, attempt: 1, has_chat: true }, qc);
    await flushPromises();
    await flushPromises();

    const scrollerEl = wrapper.find("[data-testid='agent-chat-tail'] > div.overflow-auto")
      .element as HTMLElement;
    // Pin layout so the user is "at the bottom" (distance 0 ≤ 50).
    Object.defineProperty(scrollerEl, "scrollHeight", { configurable: true, value: 200 });
    Object.defineProperty(scrollerEl, "clientHeight", { configurable: true, value: 100 });
    scrollerEl.scrollTop = 100;

    const queryKey = ["jobs", "j", "nodes", "n", "iter", "top", "chat", 1];
    qc.setQueryData(queryKey, {
      turns: [
        ...initialTurns,
        {
          type: "assistant",
          message: { role: "assistant", content: [{ type: "text", text: "Second" }] },
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    await flushPromises();

    // The new turn rendered and the watcher pulled the scroller back
    // to the bottom — scrollTop ends at scrollHeight (auto-scroll).
    expect(wrapper.html()).toContain("Second");
    expect(scrollerEl.scrollTop).toBe(scrollerEl.scrollHeight);
  });

  it("preserves scroll position when user is scrolled up and new turn arrives", async () => {
    // Initial chat: a few text turns the user is reading from earlier.
    const initialTurns = [
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "First" }] },
      },
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "Second" }] },
      },
    ];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = mountWithFetch({ turns: initialTurns, attempt: 1, has_chat: true }, qc);
    await flushPromises();
    await flushPromises();

    // Find the scroll container and pin its layout: 200px content,
    // 100px viewport, scrolled to the very top (200 - 100 - 0 = 200px
    // from bottom, far above the 50px auto-scroll threshold).
    const scrollerEl = wrapper.find("[data-testid='agent-chat-tail'] > div.overflow-auto")
      .element as HTMLElement;
    expect(scrollerEl).toBeTruthy();
    Object.defineProperty(scrollerEl, "scrollHeight", { configurable: true, value: 200 });
    Object.defineProperty(scrollerEl, "clientHeight", { configurable: true, value: 100 });
    scrollerEl.scrollTop = 0;

    // A new turn lands; the SSE invalidation path would refetch the
    // chat endpoint and replace the cache. Simulate that by writing
    // directly into the matching query key.
    const queryKey = ["jobs", "j", "nodes", "n", "iter", "top", "chat", 1];
    qc.setQueryData(queryKey, {
      turns: [
        ...initialTurns,
        {
          type: "assistant",
          message: { role: "assistant", content: [{ type: "text", text: "Third" }] },
        },
      ],
      attempt: 1,
      has_chat: true,
    });
    await flushPromises();
    await flushPromises();

    // The new turn renders…
    const html = wrapper.html();
    expect(html).toContain("Third");
    // …but the user's scrollTop is preserved (NOT yanked to the
    // bottom). 0 was set above; auto-scroll would have set it to 200.
    expect(scrollerEl.scrollTop).toBe(0);
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
