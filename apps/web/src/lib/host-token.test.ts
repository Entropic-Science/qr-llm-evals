import { afterEach } from "vitest";
import { clearHostToken, getHostToken, setHostToken } from "./host-token";

describe("host token storage", () => {
  const own = Object.getOwnPropertyDescriptor(window, "sessionStorage");
  afterEach(() => { if (own) Object.defineProperty(window, "sessionStorage", own); else Reflect.deleteProperty(window, "sessionStorage"); });

  it("uses sessionStorage scoped to the run and can clear it", () => {
    setHostToken("run_a", "secret-a");
    setHostToken("run_b", "secret-b");
    expect(getHostToken("run_a")).toBe("secret-a");
    clearHostToken("run_a");
    expect(getHostToken("run_a")).toBeUndefined();
    expect(getHostToken("run_b")).toBe("secret-b");
  });

  it("reads as no token, and never throws, where the browser refuses access to its storage altogether", () => {
    // With all site data blocked, reading `window.sessionStorage` is itself what throws. The run page then
    // opens as a viewer; it must not fail to draw.
    Object.defineProperty(window, "sessionStorage", { configurable: true, get: () => { throw new DOMException("The operation is insecure.", "SecurityError"); } });
    expect(() => setHostToken("run_a", "secret-a")).not.toThrow();
    expect(getHostToken("run_a")).toBeUndefined();
    expect(() => clearHostToken("run_a")).not.toThrow();
  });
});
