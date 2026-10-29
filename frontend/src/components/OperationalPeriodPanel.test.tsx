import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import OperationalPeriodPanel from "./OperationalPeriodPanel";
import { nextPeriodDate } from "../utils/time";
import { makeIncident, makeOperationalPeriod, type IncidentSession, type OperationalPeriod } from "../types/incident";

function renderPanel(incident: IncidentSession, activePeriod: OperationalPeriod | null) {
  const onAdvancePeriod = vi.fn();
  render(
    <OperationalPeriodPanel
      incident={incident}
      activePeriod={activePeriod}
      onPeriodSelect={vi.fn()}
      onAdvancePeriod={onAdvancePeriod}
      onUpdateName={vi.fn()}
    />,
  );
  return { onAdvancePeriod };
}

describe("OperationalPeriodPanel", () => {
  it("shows the day tabs and the burned area of the period's last frame", () => {
    const incident = makeIncident("River Valley Fire");
    const p = incident.operationalPeriods[0];
    p.date = "2026-10-11";
    p.frameSummaries = [
      { timeHours: 1, areaHa: 2.04, headRosMMin: 1, maxHfiKwM: 1, fireType: "surface", flameLengthM: 1 },
      { timeHours: 4, areaHa: 61.37, headRosMMin: 1, maxHfiKwM: 1, fireType: "surface", flameLengthM: 1 },
    ];
    const { onAdvancePeriod } = renderPanel(incident, p);
    expect(screen.getByRole("button", { name: /Day 1/ })).toHaveTextContent("10-11");
    expect(screen.getByText("61.4 ha burned")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ New Period" }));
    expect(screen.getByLabelText("Date")).toHaveValue("2026-10-12");
    fireEvent.click(screen.getByRole("button", { name: "Advance" }));
    expect(onAdvancePeriod).toHaveBeenCalledWith({ date: "2026-10-12", opPeriodStart: "08:00", opPeriodEnd: "20:00" });
  });

  it("renders an incident stored by an older version (missing/odd fields) without throwing", () => {
    const legacy = {
      ...makeOperationalPeriod(1, "2026-07-01"),
      date: undefined,
      frameSummaries: undefined,
    } as unknown as OperationalPeriod;
    const noArea = {
      ...makeOperationalPeriod(2, "not-a-date"),
      frameSummaries: [{ timeHours: 1 }],
    } as unknown as OperationalPeriod;
    const incident = { ...makeIncident("Old fire"), operationalPeriods: [legacy, noArea], activePeriodIndex: 1 };
    renderPanel(incident, noArea);
    expect(screen.getByRole("button", { name: /Day 2/ })).toBeInTheDocument();
    expect(screen.queryByText(/ha burned/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ New Period" }));
    expect(screen.getByLabelText("Date")).toHaveValue(nextPeriodDate(null));
  });

  it("nextPeriodDate: the next calendar day, or today for a missing/invalid date", () => {
    expect(nextPeriodDate("2026-10-31")).toBe("2026-11-01");
    expect(nextPeriodDate("2026-12-31")).toBe("2027-01-01");
    expect(nextPeriodDate("2028-02-28")).toBe("2028-02-29");
    // 2026-10-12 03:00 UTC is still 2026-10-11 in Edmonton
    const now = Date.UTC(2026, 9, 12, 3, 0);
    expect(nextPeriodDate("garbage", now)).toBe("2026-10-11");
    expect(nextPeriodDate(undefined, now)).toBe("2026-10-11");
  });
});

describe("OperationalPeriodPanel incident name (E9, 2026-10-24)", () => {
  function setup() {
    const incident = makeIncident("Old name");
    const onUpdateName = vi.fn();
    render(
      <OperationalPeriodPanel
        incident={incident}
        activePeriod={incident.operationalPeriods[0]}
        onPeriodSelect={vi.fn()}
        onAdvancePeriod={vi.fn()}
        onUpdateName={onUpdateName}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Old name/ }));
    return { onUpdateName, input: screen.getByLabelText("Incident name") as HTMLInputElement };
  }

  it("can be erased to empty while editing; blur stores 'Untitled incident'", () => {
    const { onUpdateName, input } = setup();
    fireEvent.change(input, { target: { value: "" } });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Incident name");
    expect(onUpdateName).not.toHaveBeenCalled();
    fireEvent.blur(input);
    expect(onUpdateName).toHaveBeenCalledWith("Untitled incident");
  });

  it("Enter commits a new name; Esc cancels", () => {
    const { onUpdateName, input } = setup();
    fireEvent.change(input, { target: { value: "Millcreek" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onUpdateName).toHaveBeenCalledWith("Millcreek");
    fireEvent.click(screen.getByRole("button", { name: /Old name/ }));
    const again = screen.getByLabelText("Incident name");
    fireEvent.change(again, { target: { value: "" } });
    fireEvent.keyDown(again, { key: "Escape" });
    expect(onUpdateName).toHaveBeenCalledTimes(1);
  });
});
