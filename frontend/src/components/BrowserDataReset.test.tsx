import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import BrowserDataReset, { RESET_BUTTON_LABEL } from "./BrowserDataReset";
import { makeIncident } from "../types/incident";

function seed() {
  const a = { ...makeIncident("Millcreek"), id: "inc-a" };
  const b = { ...makeIncident("TEST"), id: "inc-b", status: "closed" as const };
  localStorage.setItem("firesim-v3-incidents", JSON.stringify([a, b]));
  localStorage.setItem("firesim.setup.inc-a", "{}");
  localStorage.setItem("firesim-v3-layout", "{}");
  localStorage.setItem("unrelated", "keep");
}

function open(onCleared = vi.fn(), onExport = vi.fn()) {
  render(<BrowserDataReset onCleared={onCleared} onExportIncident={onExport} />);
  fireEvent.click(screen.getByRole("button", { name: `${RESET_BUTTON_LABEL}…` }));
  return { onCleared, onExport, dialog: screen.getByRole("dialog", { name: RESET_BUTTON_LABEL }) };
}

describe("BrowserDataReset dialog (E9)", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    seed();
  });

  it("renders the inventory, the per-incident export and the server-archive note", () => {
    const { dialog, onExport } = open();
    const list = within(dialog).getByTestId("bdr-inventory");
    expect(list).toHaveTextContent("2 incidents: Millcreek (active), TEST (closed)");
    expect(list).toHaveTextContent("Restored Setup for 1 incident");
    expect(list).toHaveTextContent("Map layout");
    expect(dialog).toHaveTextContent("Runs kept on the server (Setup → Saved runs) are not affected");
    fireEvent.click(within(dialog).getByRole("button", { name: "Export incident Millcreek as JSON" }));
    expect(onExport).toHaveBeenCalledWith(expect.objectContaining({ id: "inc-a", name: "Millcreek" }));
    expect(localStorage.getItem("firesim-v3-incidents")).not.toBeNull();
  });

  it("confirm clears the FireSim keys only and calls onCleared", () => {
    const { dialog, onCleared } = open();
    fireEvent.click(within(dialog).getByRole("button", { name: "Clear and reload" }));
    expect(onCleared).toHaveBeenCalledTimes(1);
    expect(onCleared.mock.calls[0][0].removed).toHaveLength(3);
    expect(localStorage.getItem("firesim-v3-incidents")).toBeNull();
    expect(localStorage.getItem("firesim.setup.inc-a")).toBeNull();
    expect(localStorage.getItem("unrelated")).toBe("keep");
  });

  it("cancel (and Esc) close without clearing", () => {
    const { dialog, onCleared } = open();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `${RESET_BUTTON_LABEL}…` }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onCleared).not.toHaveBeenCalled();
    expect(localStorage.getItem("firesim-v3-incidents")).not.toBeNull();
  });

  it("says when nothing is stored and disables the clear", () => {
    localStorage.clear();
    const { dialog } = open();
    expect(within(dialog).getByTestId("bdr-empty")).toHaveTextContent("Nothing is stored");
    expect(within(dialog).getByRole("button", { name: "Clear and reload" })).toBeDisabled();
  });
});
