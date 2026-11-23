import { focusField, locateField } from "./focus";

function page(html: string): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = html;
  document.body.append(root);
  return root;
}

afterEach(() => { document.body.innerHTML = ""; });

const STEP = `
  <fieldset id="panel-claim"><div class="field" data-path="design.claim"><input type="radio" name="claim" aria-label="A difference only"><button type="button">chip</button></div></fieldset>
  <fieldset id="panel-prompts"><details><summary>Other evals</summary>
    <div class="field" data-path="cards[].overrides"><table><tbody>
      <tr><th>First<small><code>novelty_bench</code></small></th><td><button type="button" id="chip-a">chip</button></td></tr>
      <tr><th>Second<small><code>creative_diversity</code></small></th><td><input id="prompts-b" type="number"></td></tr>
    </tbody></table></div>
  </details></fieldset>
  <fieldset id="panel-margins"><fieldset class="field" data-path="design.margins"><input id="margin" type="number"></fieldset></fieldset>
  <fieldset id="panel-comparison"><p>These arms were written by hand.</p><button type="button" id="rebuild">Rebuild from roles</button></fieldset>
`;

describe("locateField", () => {
  it("finds the field that carries the path", () => {
    const root = page(STEP);
    expect(locateField(root, "design.claim")?.dataset.path).toBe("design.claim");
  });

  it("finds the row of one eval for the path of its prompts or its repetitions", () => {
    const root = page(STEP);
    expect(locateField(root, "cards.creative_diversity.items")?.querySelector("code")?.textContent).toBe("creative_diversity");
    expect(locateField(root, "cards.novelty_bench.k")?.querySelector("code")?.textContent).toBe("novelty_bench");
    // An eval the table does not list leads to the table.
    expect(locateField(root, "cards.unknown_eval.items")?.dataset.path).toBe("cards[].overrides");
  });

  it("falls back to the field of the block the setting sits in, then to the panel that edits it", () => {
    const root = page(STEP);
    expect(locateField(root, "design.margins.altered_state_meq/meq30_total")?.dataset.path).toBe("design.margins");
    expect(locateField(root, "arms")?.id).toBe("panel-comparison");
  });

  it("returns null for a setting that is not drawn there", () => {
    expect(locateField(page("<p>Nothing here.</p>"), "design.alpha")).toBeNull();
  });
});

describe("focusField", () => {
  it("focuses the field's own control before its chip", () => {
    const root = page(STEP);
    focusField(locateField(root, "design.claim")!);
    expect(document.activeElement).toHaveAttribute("aria-label", "A difference only");
  });

  it("unfolds the details a field is folded away in", () => {
    const root = page(STEP);
    const details = root.querySelector("details")!;
    expect(details.open).toBe(false);
    focusField(locateField(root, "cards.creative_diversity.items")!);
    expect(details.open).toBe(true);
    expect(document.activeElement?.id).toBe("prompts-b");
  });

  it("focuses a button where the field has nothing to type into, and the element itself where it has no control", () => {
    const root = page(STEP);
    focusField(locateField(root, "cards.novelty_bench.items")!);
    expect(document.activeElement?.id).toBe("chip-a");
    focusField(locateField(root, "arms")!);
    expect(document.activeElement?.id).toBe("rebuild");

    const bare = page("<h2 id=\"heading\">What is compared</h2>").querySelector<HTMLElement>("h2")!;
    focusField(bare, "start");
    expect(document.activeElement).toBe(bare);
    expect(bare).toHaveAttribute("tabindex", "-1");
  });
});
