import { cards } from "@qr-evals/contracts";
import { fireEvent, render, screen } from "@testing-library/react";
import { studyOneTemplate } from "../lib/config-store";
import { REC_STATES, rate, recFor, type Rec, type RecState } from "../lib/recommendations";
import { RecChip, RecGlyph } from "./RecChip";

const rec = (patch: Partial<Rec> = {}): Rec => ({
  path: "cards.self_referential_probe.items", label: "Prompts", value: "30 prompts", recommended: "30", state: "sufficient",
  have: "30 prompts detect ≥ 0.044 at 80% power.", need: "±0.05 needs ≥ 24.",
  basis: "Paired sign-flip test, Holm over 3 tests (α′ = 0.0167), worst-case variance.",
  stepId: "design", nodeId: "card:self_referential_probe", ...patch,
});

describe("RecChip", () => {
  it("is a button that says the setting, its value, its state and what it needs", () => {
    render(<RecChip rec={rec()} />);
    const chip = screen.getByRole("button", { name: "Prompts: 30 prompts. Enough for the stated margin. ±0.05 needs ≥ 24." });
    expect(chip).toHaveClass("rec", "rec--sufficient");
    expect(chip).toHaveAttribute("type", "button");
    expect(chip).toHaveTextContent("30 prompts");
    const glyph = chip.querySelector(".rec__glyph");
    expect(glyph).toHaveTextContent("✓");
    expect(glyph).toHaveAttribute("aria-hidden", "true");
  });

  it("describes itself with a tooltip of three lines: have, need, basis", () => {
    render(<RecChip rec={rec()} />);
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(tip).toHaveClass("rec-tip");
    expect([...tip.children].map((line) => line.textContent)).toEqual([
      "30 prompts detect ≥ 0.044 at 80% power.",
      "±0.05 needs ≥ 24.",
      "Paired sign-flip test, Holm over 3 tests (α′ = 0.0167), worst-case variance.",
    ]);
    expect(screen.getByRole("button")).toHaveAttribute("aria-describedby", tip.id);
    expect(screen.getByRole("button")).toHaveAccessibleDescription(/30 prompts detect ≥ 0\.044 at 80% power\./);
  });

  it("pins its tooltip open on a click, and closes it on Escape, on a second click and on blur", () => {
    const behind = vi.fn();
    render(<div onKeyDown={behind}><RecChip rec={rec()} /></div>);
    const chip = screen.getByRole("button");
    const anchor = chip.parentElement!;
    expect(chip).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(anchor).toHaveClass("is-pinned");
    fireEvent.keyDown(chip, { key: "Escape" });
    expect(chip).toHaveAttribute("aria-expanded", "false");
    // Escape closed the tooltip and went no further: a dialog around the chip stays open.
    expect(behind).not.toHaveBeenCalled();
    fireEvent.keyDown(chip, { key: "Escape" });
    expect(behind).toHaveBeenCalledTimes(1);

    fireEvent.click(chip);
    fireEvent.click(chip);
    expect(anchor).not.toHaveClass("is-pinned");
    fireEvent.click(chip);
    fireEvent.blur(chip);
    expect(anchor).not.toHaveClass("is-pinned");
  });

  it("hands its rec to onActivate instead, and then claims no expanded state", () => {
    const onActivate = vi.fn();
    const item = rec({ state: "custom", value: "k = 25" });
    render(<RecChip rec={item} onActivate={onActivate} />);
    const chip = screen.getByRole("button");
    expect(chip).not.toHaveAttribute("aria-expanded");
    fireEvent.click(chip);
    expect(onActivate).toHaveBeenCalledWith(item);
    expect(chip.parentElement).not.toHaveClass("is-pinned");
  });

  it.each(Object.keys(REC_STATES) as RecState[])("draws the %s state with its class, its mark and its legend copy", (state) => {
    render(<RecChip rec={rec({ state })} />);
    const chip = screen.getByRole("button");
    expect(chip).toHaveClass(REC_STATES[state].className);
    expect(chip.className.match(/rec--/g)).toHaveLength(1);
    expect(chip.getAttribute("aria-label")).toContain(`. ${REC_STATES[state].legend}. `);
    const glyph = chip.querySelector(".rec__glyph")!;
    if (REC_STATES[state].glyph === null) expect(glyph.querySelector("svg")).not.toBeNull();
    else expect(glyph).toHaveTextContent(REC_STATES[state].glyph);
  });

  it("draws a padlock, not a character, for what the harness sets", () => {
    const { container } = render(<RecGlyph state="locked" />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container).toHaveTextContent("");
  });

  it("draws the recs of a real study: the probe green, PIT and MMD never", () => {
    const recs = rate(studyOneTemplate(), cards);
    render(<>
      <RecChip rec={recFor(recs, "cards.self_referential_probe.items")!} />
      <RecChip rec={recFor(recs, "cards.tier0_pit_residuals.items")!} />
      <RecChip rec={recFor(recs, "cards.tier0_mmd_hamming.items")!} />
    </>);
    const [probe, pit, mmd] = screen.getAllByRole("button");
    expect(probe).toHaveClass("rec--sufficient");
    for (const chip of [pit!, mmd!]) {
      expect(chip).toHaveClass("rec--unknown");
      expect(chip).not.toHaveClass("rec--sufficient");
      // A KS or MMD outcome has no margin, so no positive control bears on it: the chip says only that it is not rated.
      expect(chip).toHaveAccessibleName(/No closed-form power\. Its sample size is not rated\.$/);
    }
  });
});
