import { clearHostToken, getHostToken, setHostToken } from "./host-token";

describe("host token storage", () => {
  it("uses sessionStorage scoped to the run and can clear it", () => {
    setHostToken("run_a", "secret-a");
    setHostToken("run_b", "secret-b");
    expect(getHostToken("run_a")).toBe("secret-a");
    clearHostToken("run_a");
    expect(getHostToken("run_a")).toBeUndefined();
    expect(getHostToken("run_b")).toBe("secret-b");
  });
});
