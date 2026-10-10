import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import InfoTip, { TIP_OPEN_DELAY_MS, TipButton } from "./InfoTip";
import Badge from "./Badge";
import FwiClassChip from "./FwiClassChip";
import HfiClassChip from "./HfiClassChip";
import { TIPS } from "../content/explanations";
import { HFI_CLASS_CAVEAT } from "../utils/fireClasses";

const tipOf = (trigger: HTMLElement) =>
  document.getElementById(trigger.getAttribute("aria-describedby") ?? trigger.getAttribute("aria-controls") ?? "")!;

afterEach(() => {
  vi.useRealTimers();
});

describe("InfoTip", () => {
  it("is a button with an accessible name, linked to a role=tooltip pop-up by aria-describedby", () => {
    render(<InfoTip label="About P10" text="P10 explanation" />);
    const btn = screen.getByRole("button", { name: "About P10" });
    const tip = tipOf(btn);
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip).toHaveTextContent("P10 explanation");
    expect(tip).toHaveAttribute("popover", "manual");
    expect(tip).not.toHaveClass("open");
  });

  it("opens on hover after the delay, stays while the pointer is over the pop-up, closes after leaving", () => {
    vi.useFakeTimers();
    render(<InfoTip label="About X" text="Tip" />);
    const btn = screen.getByRole("button", { name: "About X" });
    const tip = tipOf(btn);
    fireEvent.pointerEnter(btn);
    act(() => vi.advanceTimersByTime(TIP_OPEN_DELAY_MS - 50));
    expect(tip).not.toHaveClass("open");
    act(() => vi.advanceTimersByTime(60));
    expect(tip).toHaveClass("open");
    // Moving from the trigger onto the pop-up keeps it open (WCAG 1.4.13 hoverable)
    fireEvent.pointerLeave(btn);
    fireEvent.pointerEnter(tip);
    act(() => vi.advanceTimersByTime(1000));
    expect(tip).toHaveClass("open");
    fireEvent.pointerLeave(tip);
    act(() => vi.advanceTimersByTime(1000));
    expect(tip).not.toHaveClass("open");
  });

  it("opens on keyboard focus, closes on Escape and on blur", () => {
    render(
      <>
        <InfoTip label="About X" text="Tip" />
        <button type="button">Next</button>
      </>,
    );
    const btn = screen.getByRole("button", { name: "About X" });
    const tip = tipOf(btn);
    fireEvent.focus(btn);
    expect(tip).toHaveClass("open");
    fireEvent.keyDown(btn, { key: "Escape" });
    expect(tip).not.toHaveClass("open");
    fireEvent.focus(btn);
    expect(tip).toHaveClass("open");
    fireEvent.blur(btn, { relatedTarget: screen.getByRole("button", { name: "Next" }) });
    expect(tip).not.toHaveClass("open");
  });

  it("a tap or click pins it open; an outside click closes it", () => {
    vi.useFakeTimers();
    render(
      <>
        <InfoTip label="About X" text="Tip" />
        <p>outside</p>
      </>,
    );
    const btn = screen.getByRole("button", { name: "About X" });
    const tip = tipOf(btn);
    fireEvent.click(btn);
    expect(tip).toHaveClass("open");
    fireEvent.pointerLeave(btn);
    act(() => vi.advanceTimersByTime(1000));
    expect(tip).toHaveClass("open");
    fireEvent.pointerDown(screen.getByText("outside"));
    expect(tip).not.toHaveClass("open");
    // A second click on a pinned tip closes it
    fireEvent.click(btn);
    expect(tip).toHaveClass("open");
    fireEvent.click(btn);
    expect(tip).not.toHaveClass("open");
  });

  it("ignores touch hover (a tap toggles instead)", () => {
    vi.useFakeTimers();
    render(<InfoTip label="About X" text="Tip" />);
    const btn = screen.getByRole("button", { name: "About X" });
    fireEvent.pointerEnter(btn, { pointerType: "touch" });
    act(() => vi.advanceTimersByTime(1000));
    expect(tipOf(btn)).not.toHaveClass("open");
  });

  it("interactive tips (with links) have no tooltip role; the trigger has aria-expanded/aria-controls", () => {
    render(
      <InfoTip label="About the burning period" interactive>
        Text <a href="https://example.org/validation">Evidence</a>
      </InfoTip>,
    );
    const btn = screen.getByRole("button", { name: "About the burning period" });
    expect(btn).not.toHaveAttribute("aria-describedby");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    const pop = tipOf(btn);
    expect(pop).not.toHaveAttribute("role");
    fireEvent.focus(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    // Focus can move into the pop-up without closing it
    const link = screen.getByRole("link", { name: "Evidence", hidden: true });
    fireEvent.blur(btn, { relatedTarget: link });
    expect(pop).toHaveClass("open");
    // Escape from inside returns focus to the trigger
    link.focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(pop).not.toHaveClass("open");
    expect(btn).toHaveFocus();
  });

  it("uses the Popover API (top layer) when the browser has it", () => {
    const show = vi.fn();
    const hide = vi.fn();
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    const had = { show: proto.showPopover, hide: proto.hidePopover };
    proto.showPopover = show;
    proto.hidePopover = hide;
    try {
      render(<InfoTip label="About X" text="Tip" />);
      fireEvent.focus(screen.getByRole("button", { name: "About X" }));
      expect(show).toHaveBeenCalled();
    } finally {
      proto.showPopover = had.show;
      proto.hidePopover = had.hide;
    }
  });
});

describe("Badge", () => {
  it("without a tip is a plain chip", () => {
    render(<Badge tone="warn">Illustrative</Badge>);
    expect(screen.getByText("Illustrative").tagName).toBe("SPAN");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("with a tip is a button named by its text whose explanation opens on focus", () => {
    render(<Badge tone="warn" tip={TIPS.rangeTooNarrow}>Range too narrow</Badge>);
    const b = screen.getByRole("button", { name: "Range too narrow" });
    const tip = tipOf(b);
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip).toHaveTextContent("over-predicts one-day growth");
    fireEvent.focus(b);
    expect(tip).toHaveClass("open");
  });

  it("runs onActivate on click instead of pinning (e.g. open About & sources)", () => {
    const go = vi.fn();
    render(<Badge tip="Tip" onActivate={go}>Low one-day skill</Badge>);
    fireEvent.click(screen.getByRole("button", { name: "Low one-day skill" }));
    expect(go).toHaveBeenCalledTimes(1);
  });

  it("TipButton runs its action and explains it on focus", () => {
    const act1 = vi.fn();
    render(<TipButton tip="Download KML" onClick={act1}>KML</TipButton>);
    const b = screen.getByRole("button", { name: "KML" });
    fireEvent.focus(b);
    expect(tipOf(b)).toHaveClass("open");
    fireEvent.click(b);
    expect(act1).toHaveBeenCalled();
  });
});

describe("class chips", () => {
  it("FWI class chips say 'not an official danger rating' in their tip", () => {
    render(<FwiClassChip fwi={25} />);
    const chip = screen.getByRole("button", { name: "Very High" });
    expect(tipOf(chip)).toHaveTextContent("Not an official fire danger rating");
  });

  it("HFI class chips carry the class meaning, the C-2 caveat and the source", () => {
    render(<HfiClassChip hfi={3000} />);
    const chip = screen.getByRole("button", { name: "Class 4" });
    const tip = tipOf(chip);
    expect(tip).toHaveTextContent("Intermittent crowning");
    expect(tip).toHaveTextContent(HFI_CLASS_CAVEAT);
    expect(tip).toHaveTextContent("Cole & Alexander (1995)");
  });
});
