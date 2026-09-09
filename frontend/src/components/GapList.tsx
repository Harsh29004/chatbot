import { useEffect, useState } from "react";

import { api, type Gaps } from "../lib/api";

function relative(iso: string): string {
  const then = new Date(iso).getTime();
  const hours = Math.floor((Date.now() - then) / 36e5);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

/**
 * The questions the bot couldn't answer.
 *
 * This is the loop that makes the product get better: real users ask something
 * the sheet doesn't cover, it shows up here, the owner adds a row, the bot
 * answers it next time. Grouped and ranked by frequency, because a raw feed of
 * every miss is unreadable and nobody acts on it.
 */
export function GapList() {
  const [data, setData] = useState<Gaps | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .gaps(30)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <div className="error-box">{error}</div>;

  if (!data) {
    return (
      <div className="center" style={{ padding: "var(--s6)" }}>
        <div className="spinner wrap-center" />
      </div>
    );
  }

  if (data.gaps.length === 0) {
    return (
      <div className="empty">
        <p className="small">
          Nothing unanswered in the last {data.days} days.
        </p>
        <p className="tiny mt-3">
          When someone asks your bot something your sheet doesn't cover, it
          shows up here.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Question asked</th>
              <th style={{ width: 90 }}>Asked</th>
              <th style={{ width: 130 }}>Last</th>
              <th style={{ width: 150 }}>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {data.gaps.map((gap) => (
              <tr key={gap.question}>
                <td style={{ color: "var(--bone)" }}>{gap.question}</td>
                <td className="mono">{gap.times_asked}×</td>
                <td className="tiny">{relative(gap.last_asked)}</td>
                <td>
                  <span
                    className={`badge ${
                      gap.verdict === "nearly" ? "badge-warn" : "badge-off"
                    }`}
                    title={`Best match ${gap.best_score.toFixed(2)}`}
                  >
                    {gap.verdict === "nearly" ? "Needs phrasing" : "Not covered"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="tiny mt-4">
        <strong className="mint">Needs phrasing</strong> — your sheet nearly
        covers it; add the wording people actually used to{" "}
        <code className="mono">Alt_Phrasings</code>.{" "}
        <strong>Not covered</strong> — nothing close; add a new row.
      </p>

      {data.flagged_inputs > 0 && (
        <p className="tiny mt-3">
          {data.flagged_inputs} input
          {data.flagged_inputs === 1 ? " was" : "s were"} flagged as a probable
          prompt-injection attempt and left out of this list.
        </p>
      )}
    </>
  );
}
