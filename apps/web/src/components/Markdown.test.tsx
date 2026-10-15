import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("sanitises executable markup and unsafe links", () => {
    const { container } = render(<Markdown source={'# Safe\n<script>alert(1)</script>\n<img src="x" onerror="alert(2)">\n[bad](javascript:alert(3))'} />);
    expect(screen.getByRole("heading", { name: "Safe" })).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  });
});
