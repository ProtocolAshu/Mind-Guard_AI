import { DECISION_LABEL, RESTRICTIVE } from "@/lib/format";
import type { InterventionType } from "@/lib/types";

export function DecisionTag({ decision }: { decision: InterventionType }) {
  const tone = decision === "ALLOW" ? "tag-growth" : RESTRICTIVE.has(decision) ? "tag-alarm" : "tag-signal";
  return <span className={`tag ${tone}`}>{DECISION_LABEL[decision]}</span>;
}
