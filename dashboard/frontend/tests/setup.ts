// Vitest setup — runs once before each test file.
// Polyfills the browser-only globals jsdom doesn't provide.

class MockEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  readyState = 0;
  constructor(url: string) {
    this.url = url;
  }
  close(): void {
    this.readyState = 2;
  }
}

(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
