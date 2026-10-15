import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { PresenceList, presenceFromEvent } from "./PresenceList";
import { Questionnaire } from "./Questionnaire";
import { SessionControls } from "./SessionControls";
import { Timer, formatRemaining } from "./Timer";
import { DEFAULT_DISPLAY_SETTINGS, loadDisplaySettings, saveDisplaySettings } from "../../lib/supervised-settings";

describe("supervised instrument invariants", () => {
  it("formats an advisory timer below zero", () => {
    expect(formatRemaining(-61)).toBe("−01:01");
    render(<Timer label="Advisory pause" remaining={-1} />);
    expect(screen.getByRole("timer")).toHaveAccessibleName("Advisory pause: −00:01");
  });

  it("never invokes start or resume from a state render", () => {
    const onAction = vi.fn();
    const view = render(<SessionControls state="ready" preComplete onAction={onAction} />);
    view.rerender(<SessionControls state="refocus" preComplete readyAction="resume" onAction={onAction} />);
    view.rerender(<SessionControls state="ready" preComplete readyAction="resume" onAction={onAction} />);
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onAction).toHaveBeenCalledWith("resume");
  });

  it("keeps the pre questionnaire gated until every required answer exists", () => {
    const submit = vi.fn();
    render(<Questionnaire phase="pre" onSubmit={submit} />);
    const button = screen.getByRole("button", { name: "Submit pre-session responses" });
    expect(button).toBeDisabled();
    for (const name of ["influence_belief", "calmness", "focus"]) fireEvent.click(document.querySelector(`input[name="${name}"]`) as HTMLInputElement);
    expect(button).toBeEnabled(); fireEvent.click(button); expect(submit).toHaveBeenCalledTimes(1);
  });

  it("persists single and tiled preferences independently", () => {
    const values = new Map<string, string>();
    const storage: Storage = { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
    const single = { ...DEFAULT_DISPLAY_SETTINGS.single, text_size: "lg" as const, show_thinking: true };
    const tiled = { ...DEFAULT_DISPLAY_SETTINGS.tiled, text_size: "sm" as const, show_thinking: false };
    expect(saveDisplaySettings("single", single, storage)).toBe(true);
    expect(saveDisplaySettings("tiled", tiled, storage)).toBe(true);
    expect(loadDisplaySettings("single", storage)).toEqual(single);
    expect(loadDisplaySettings("tiled", storage)).toEqual(tiled);
  });

  it("populates presence from observer.presence event data", () => {
    const observers = presenceFromEvent({ session_id: "s1", present: [{ observer_id: "o1", name: "Aster" }] });
    render(<PresenceList observers={observers} />);
    expect(screen.getByText("Aster")).toBeInTheDocument();
    expect(screen.getByText("o1")).toBeInTheDocument();
  });
});
