import { describe, expect, it } from "vitest";
import { iterToken } from "@/api/paths";

describe("iterToken", () => {
  it("returns 'top' for an empty path", () => {
    expect(iterToken([])).toBe("top");
  });

  it("returns 'i<n>' for a single-element path", () => {
    expect(iterToken([0])).toBe("i0");
    expect(iterToken([7])).toBe("i7");
  });

  it("joins nested components with underscores", () => {
    expect(iterToken([0, 1])).toBe("i0_1");
    expect(iterToken([2, 0, 4])).toBe("i2_0_4");
  });

  it("throws on negative or non-integer components", () => {
    expect(() => iterToken([-1])).toThrow();
    expect(() => iterToken([1.5])).toThrow();
  });
});
