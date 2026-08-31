import { bytes } from "../api";

/* Reads as a gauge rather than a sentence: this is the number that decides
   whether the next download happens at all. */
export default function QuotaBar({ used, quota }: { used: number; quota: number }) {
  const frac = quota > 0 ? Math.min(1, used / quota) : 0;
  const tone = frac >= 1 ? "full" : frac >= 0.85 ? "warn" : "";
  return (
    <div className="quota">
      <div className="quota-head">
        <span className="micro">Space used</span>
        <span className="num">{bytes(used)}</span>
        <span className="right">of {bytes(quota)}</span>
      </div>
      <div
        className={`gauge ${tone}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(frac * 100)}
      >
        <i style={{ transform: `scaleX(${frac})` }} />
      </div>
    </div>
  );
}
