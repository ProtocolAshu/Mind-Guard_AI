import { describe, expect, it } from "vitest";
import { hourRanges, minutes, percent, reasonText, riskBand } from "@/lib/format";

describe("format", () => {
  it("formats minutes", () => {
    expect(minutes(42.4)).toBe("42 min");
    expect(minutes(125)).toBe("2 h 5 min");
    expect(minutes(120)).toBe("2 h");
    expect(minutes(null)).toBe("—");
  });
  it("formats percentages", () => {
    expect(percent(0.426)).toBe("43%");
    expect(percent(undefined)).toBe("—");
  });
  it("groups trigger hours into ranges, including across midnight", () => {
    expect(hourRanges([22, 23, 0])).toEqual(["10 PM–1 AM"]);
    expect(hourRanges([9, 14, 15])).toEqual(["9 AM–10 AM", "2 PM–4 PM"]);
    expect(hourRanges([])).toEqual([]);
  });
  it("bands risk exactly like the backend (0.45 / 0.60 / 0.75), including boundaries", () => {
    expect(riskBand(44.9)).toBe("low");
    expect(riskBand(45)).toBe("mild");
    expect(riskBand(59.9)).toBe("mild");
    expect(riskBand(60)).toBe("elevated");
    expect(riskBand(74.9)).toBe("elevated");
    expect(riskBand(75)).toBe("high");
    expect(riskBand(null)).toBeNull();
  });
  it("explains reason codes in plain language", () => {
    expect(reasonText("STYLE_CEILING")).toBe("Softened to match your intervention style");
    expect(reasonText("SOMETHING_NEW")).toBe("something new");
  });
});
