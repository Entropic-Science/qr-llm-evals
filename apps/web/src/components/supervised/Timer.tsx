export function formatRemaining(totalSeconds: number): string {
  const rounded = Math.floor(Math.abs(totalSeconds));
  const minutes = Math.floor(rounded / 60);
  const seconds = rounded % 60;
  return `${totalSeconds < 0 ? "−" : ""}${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function Timer({ remaining, label, anchor = false }: { remaining: number; label: string; anchor?: boolean }) {
  return <div className={`supervised-timer${anchor ? " supervised-timer--anchor" : ""}`} role="timer" aria-label={`${label}: ${formatRemaining(remaining)}`}>
    {anchor ? <span className="now-line" aria-hidden="true">NOW</span> : null}<small>{label}</small><strong>{formatRemaining(remaining)}</strong>
  </div>;
}
