"""Daily cattle farm report generator.

Can be used in two ways:
  1. Standalone CLI:
       uv run python -m api.report [--date YYYY-MM-DD] [--db PATH] [--output PATH]
  2. Programmatically via ReportGenerator (used by the FastAPI endpoint):
       ReportGenerator().generate_from_connection(con, target_date)

Scheduling in production
------------------------
A — OS cron (simplest, single-server):
    0 6 * * *  cd /srv/farm && uv run python -m api.report \\
               --output /var/reports/$(date +%%Y-%%m-%%d).txt >> /var/log/farm.log 2>&1

B — APScheduler inside FastAPI (self-contained, no external cron):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_report_job, "cron", hour=6, minute=0)

C — Cloud Scheduler + HTTP trigger (production-grade, retries & audit trail):
    GCP Cloud Scheduler / AWS EventBridge calls GET /reports/daily on a cron
    schedule. The endpoint generates, stores and returns the report.

Storage recommendation for this SQLite stack:
    A `report` table (report_date PK, generated_at, content) lets the API
    serve cached reports without re-running queries. Alternatively store one
    text file per day under reports/YYYY-MM-DD.txt.
"""

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------


class ReportGenerator:
    """Generates a text-based daily farm report from a SQLite database.

    The report covers three sections for a given target date:
    - Milk production per cow (daily totals on that specific date)
    - Weight summary per cow (current vs. 30-day average as of that date)
    - Health alerts (ill cows detected as of that date)
    """

    _WIDTH = 80
    _COL_NAME = 24
    _SEPARATOR = "=" * _WIDTH

    def generate(self, target_date: date, db_path: Path) -> str:
        """Generate the report by opening and closing its own connection.

        Args:
            target_date: The date the report covers.
            db_path: Path to the SQLite database file.

        Returns:
            Full plain-text report string.
        """
        con = sqlite3.connect(db_path, check_same_thread=False)
        try:
            return self.generate_from_connection(con, target_date)
        finally:
            con.close()

    def generate_from_connection(
        self, con: sqlite3.Connection, target_date: date
    ) -> str:
        """Generate the report using an already-open connection.

        Callers that already hold a connection (e.g. the FastAPI endpoint)
        should use this method to avoid opening a second connection.

        Args:
            con: Open SQLite connection.
            target_date: The date the report covers.

        Returns:
            Full plain-text report string.
        """
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        header = (
            f"Daily Cattle Farm Report — {target_date}\n"
            f"Generated: {generated_at}\n"
            f"{self._SEPARATOR}"
        )
        milk = self._milk_section(con, target_date)
        weights = self._weights_section(con, target_date)
        health = self._health_section(con, target_date)
        return "\n\n".join([header, milk, weights, health]) + f"\n{self._SEPARATOR}\n"

    # ------------------------------------------------------------------
    # Private section builders
    # ------------------------------------------------------------------

    def _milk_section(self, con: sqlite3.Connection, target_date: date) -> str:
        """Build the MILK PRODUCTION section for the target date.

        Args:
            con: Open SQLite connection.
            target_date: Date whose production is reported.

        Returns:
            Formatted section string.
        """
        ts = target_date.isoformat()
        rows = con.execute(
            """
            SELECT c.id, c.name, SUM(m.value) AS total_liters
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
              JOIN cow    c ON c.id = m.cow_id
             WHERE s.unit = 'L'
               AND DATE(m.timestamp) = ?
             GROUP BY c.id, c.name
             ORDER BY c.name
            """,
            (ts,),
        ).fetchall()

        title = f"== MILK PRODUCTION ({ts}) =="
        if not rows:
            return f"{title}\n  (no milk data for this date)"

        header = f"{'Cow':<{self._COL_NAME}} {'Total Liters':>12}"
        divider = "-" * self._COL_NAME + " " + "-" * 12
        lines = [title, header, divider]
        for _, name, liters in rows:
            lines.append(f"{name:<{self._COL_NAME}} {liters:>12.1f}")
        return "\n".join(lines)

    def _weights_section(self, con: sqlite3.Connection, target_date: date) -> str:
        """Build the WEIGHT SUMMARY section as of the target date.

        Lists every registered cow. Cows without weight measurements show '-'.
        A STATUS column shows WARNING when current weight < 95 % of 30-day avg.

        Args:
            con: Open SQLite connection.
            target_date: Reference date for current weight and 30-day window.

        Returns:
            Formatted section string.
        """
        ts = target_date.isoformat()
        rows = con.execute(
            """
            SELECT
                c.id,
                c.name,
                (
                    SELECT m.value
                      FROM measurement m
                      JOIN sensor s ON s.id = m.sensor_id
                     WHERE m.cow_id = c.id
                       AND s.unit   = 'kg'
                       AND DATE(m.timestamp) <= ?
                     ORDER BY m.timestamp DESC
                     LIMIT 1
                ) AS current_weight_kg,
                (
                    SELECT AVG(m.value)
                      FROM measurement m
                      JOIN sensor s ON s.id = m.sensor_id
                     WHERE m.cow_id = c.id
                       AND s.unit   = 'kg'
                       AND DATE(m.timestamp) >= date(?, '-30 days')
                       AND DATE(m.timestamp) <= ?
                ) AS avg_weight_30d_kg
              FROM cow c
             ORDER BY c.name
            """,
            (ts, ts, ts),
        ).fetchall()

        title = "== WEIGHT SUMMARY =="
        col_cur = 15
        col_avg = 16
        col_st = 8
        header = (
            f"{'Cow':<{self._COL_NAME}} "
            f"{'Current (kg)':>{col_cur}} "
            f"{'30-Day Avg (kg)':>{col_avg}} "
            f"{'Status':<{col_st}}"
        )
        divider = (
            "-" * self._COL_NAME
            + " "
            + "-" * col_cur
            + " "
            + "-" * col_avg
            + " "
            + "-" * col_st
        )
        lines = [title, header, divider]
        for _, name, current, avg in rows:
            if current is None or avg is None:
                cur_s = f"{'--':>{col_cur}}"
                avg_s = f"{'--':>{col_avg}}"
                status = "N/A"
            else:
                cur_s = f"{current:>{col_cur}.1f}"
                avg_s = f"{avg:>{col_avg}.1f}"
                status = "WARNING" if current < 0.95 * avg else "OK"
            lines.append(f"{name:<{self._COL_NAME}} {cur_s} {avg_s} {status:<{col_st}}")
        return "\n".join(lines)

    def _health_section(self, con: sqlite3.Connection, target_date: date) -> str:
        """Build the HEALTH ALERTS section as of the target date.

        Flags cows whose recent milk production dropped below 70 % of
        baseline, or whose current weight dropped below 95 % of baseline.
        Baseline = days 4–30 before target; recent = last 3 days.

        Args:
            con: Open SQLite connection.
            target_date: Reference date for the health check.

        Returns:
            Formatted section string.
        """
        ts = target_date.isoformat()

        milk_recent = {
            r[0]: r[1]
            for r in con.execute(
                """
                SELECT cow_id, AVG(daily_total)
                  FROM (
                        SELECT m.cow_id, DATE(m.timestamp) AS day, SUM(m.value) AS daily_total
                          FROM measurement m
                          JOIN sensor s ON s.id = m.sensor_id
                         WHERE s.unit = 'L'
                           AND DATE(m.timestamp) >  date(?, '-3 days')
                           AND DATE(m.timestamp) <= ?
                         GROUP BY m.cow_id, day
                       )
                 GROUP BY cow_id
                """,
                (ts, ts),
            ).fetchall()
        }

        milk_baseline = {
            r[0]: r[1]
            for r in con.execute(
                """
                SELECT cow_id, AVG(daily_total)
                  FROM (
                        SELECT m.cow_id, DATE(m.timestamp) AS day, SUM(m.value) AS daily_total
                          FROM measurement m
                          JOIN sensor s ON s.id = m.sensor_id
                         WHERE s.unit = 'L'
                           AND DATE(m.timestamp) >= date(?, '-30 days')
                           AND DATE(m.timestamp) <= date(?, '-3 days')
                         GROUP BY m.cow_id, day
                       )
                 GROUP BY cow_id
                """,
                (ts, ts),
            ).fetchall()
        }

        weight_current = {
            r[0]: r[1]
            for r in con.execute(
                """
                SELECT cow_id, value
                  FROM (
                        SELECT m.cow_id, m.value,
                               ROW_NUMBER() OVER (PARTITION BY m.cow_id ORDER BY m.timestamp DESC) AS rn
                          FROM measurement m
                          JOIN sensor s ON s.id = m.sensor_id
                         WHERE s.unit = 'kg'
                           AND DATE(m.timestamp) <= ?
                       )
                 WHERE rn = 1
                """,
                (ts,),
            ).fetchall()
        }

        weight_baseline = {
            r[0]: r[1]
            for r in con.execute(
                """
                SELECT m.cow_id, AVG(m.value)
                  FROM measurement m
                  JOIN sensor s ON s.id = m.sensor_id
                 WHERE s.unit = 'kg'
                   AND DATE(m.timestamp) >= date(?, '-30 days')
                   AND DATE(m.timestamp) <= date(?, '-3 days')
                 GROUP BY m.cow_id
                """,
                (ts, ts),
            ).fetchall()
        }

        cow_names = {
            r[0]: r[1] for r in con.execute("SELECT id, name FROM cow").fetchall()
        }

        all_ids = set(milk_recent) | set(weight_current)
        alerts: list[tuple[str, str, list[str]]] = []

        for cow_id in sorted(all_ids, key=lambda cid: cow_names.get(cid, cid)):
            reasons: list[str] = []

            recent = milk_recent.get(cow_id)
            baseline = milk_baseline.get(cow_id)
            if recent is not None and baseline is not None and baseline > 0:
                if recent < 0.70 * baseline:
                    drop = round((1 - recent / baseline) * 100)
                    reasons.append(
                        f"Milk production drop: recent avg {recent:.1f} L/day "
                        f"is {drop}% below baseline {baseline:.1f} L/day"
                    )

            cur_w = weight_current.get(cow_id)
            base_w = weight_baseline.get(cow_id)
            if cur_w is not None and base_w is not None and base_w > 0:
                if cur_w < 0.95 * base_w:
                    drop = round((1 - cur_w / base_w) * 100)
                    reasons.append(
                        f"Weight loss: current {cur_w:.1f} kg is "
                        f"{drop}% below 30-day baseline {base_w:.1f} kg"
                    )

            if reasons:
                alerts.append((cow_id, cow_names.get(cow_id, cow_id), reasons))

        title = "== HEALTH ALERTS =="
        if not alerts:
            return f"{title}\n  No health alerts detected."

        lines = [title]
        for cow_id, name, reasons in alerts:
            lines.append(f"  {name} ({cow_id}):")
            for r in reasons:
                lines.append(f"    - {r}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and print or save the daily report.

    Usage:
        uv run python -m api.report [--date YYYY-MM-DD] [--db PATH] [--output PATH]
    """
    parser = argparse.ArgumentParser(description="Generate daily cattle farm report.")
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=date.today().isoformat(),
        help="Target date for the report (default: today)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default="data/cows.db",
        help="Path to the SQLite database (default: data/cows.db)",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Write report to this file instead of stdout",
    )
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        parser.error(f"Invalid date format '{args.date}'. Expected YYYY-MM-DD.")

    db_path = Path(args.db)
    report = ReportGenerator().generate(target_date, db_path)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"Report written to {out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
