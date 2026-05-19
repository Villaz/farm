"""Insights endpoints: milk production, weights and health alerts."""

from fastapi import APIRouter

from api.config import (
    HEALTH_RECENT_DAYS,
    HEALTH_WINDOW_DAYS,
    MILK_DROP_THRESHOLD,
    WEIGHT_LOSS_THRESHOLD,
)
from api.dependencies import DbDep
from api.models import CowWeightSummary, IllCowEntry, MilkProductionEntry

router = APIRouter(prefix="/insights", tags=["insights"])


def _assess_milk_health(recent: float | None, baseline: float | None) -> str | None:
    if recent is None or baseline is None or baseline <= 0:
        return None
    if recent < MILK_DROP_THRESHOLD * baseline:
        drop_pct = round((1 - recent / baseline) * 100)
        return (
            f"Milk production drop: recent avg {recent:.1f} L/day is "
            f"{drop_pct}% below baseline {baseline:.1f} L/day"
        )
    return None


def _assess_weight_health(current_w: float | None, base_w: float | None) -> str | None:
    if current_w is None or base_w is None or base_w <= 0:
        return None
    if current_w < WEIGHT_LOSS_THRESHOLD * base_w:
        drop_pct = round((1 - current_w / base_w) * 100)
        return (
            f"Weight loss: current {current_w:.1f} kg is "
            f"{drop_pct}% below {HEALTH_WINDOW_DAYS}-day baseline {base_w:.1f} kg"
        )
    return None


@router.get("/milk-production", response_model=list[MilkProductionEntry])
async def get_milk_production(db: DbDep) -> list[MilkProductionEntry]:
    rows = db.execute(
        f"""
        SELECT c.id, c.name, DATE(m.timestamp) AS production_date, SUM(m.value) AS total_liters
          FROM measurement m
          JOIN sensor s ON s.id = m.sensor_id
          JOIN cow    c ON c.id = m.cow_id
         WHERE s.unit = 'L'
           AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
         GROUP BY c.id, c.name, DATE(m.timestamp)
        """
    ).fetchall()

    return [
        MilkProductionEntry(cow_id=r[0], cow_name=r[1], date=r[2], total_liters=r[3])
        for r in rows
    ]


@router.get("/weights", response_model=list[CowWeightSummary])
async def get_weights(db: DbDep) -> list[CowWeightSummary]:
    rows = db.execute(
        f"""
        WITH kg_measurements AS (
            SELECT m.cow_id, m.value, m.timestamp
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
             WHERE s.unit = 'kg'
        ),
        ranked AS (
            SELECT cow_id,
                   value,
                   ROW_NUMBER() OVER (PARTITION BY cow_id ORDER BY timestamp DESC) AS rn
              FROM kg_measurements
        ),
        current_weight AS (
            SELECT cow_id, value AS current_weight_kg
              FROM ranked
             WHERE rn = 1
        ),
        avg_weight AS (
            SELECT cow_id, AVG(value) AS avg_weight_30d_kg
              FROM kg_measurements
             WHERE timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
             GROUP BY cow_id
        )
        SELECT c.id,
               c.name,
               cw.current_weight_kg,
               aw.avg_weight_30d_kg
          FROM cow c
          LEFT JOIN current_weight cw ON cw.cow_id = c.id
          LEFT JOIN avg_weight     aw ON aw.cow_id = c.id
         ORDER BY c.name
        """
    ).fetchall()

    return [
        CowWeightSummary(
            cow_id=r[0],
            cow_name=r[1],
            current_weight_kg=r[2],
            avg_weight_30d_kg=r[3],
        )
        for r in rows
    ]


@router.get("/health", response_model=list[IllCowEntry])
async def get_health(db: DbDep) -> list[IllCowEntry]:
    rows = db.execute(
        f"""
        WITH daily_milk AS (
            SELECT m.cow_id,
                   DATE(m.timestamp) AS day,
                   SUM(m.value)      AS daily_total
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
             WHERE s.unit = 'L'
               AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
             GROUP BY m.cow_id, day
        ),
        milk_recent AS (
            SELECT cow_id, AVG(daily_total) AS avg_daily
              FROM daily_milk
             WHERE day >= DATE('now', '-{HEALTH_RECENT_DAYS} days')
             GROUP BY cow_id
        ),
        milk_baseline AS (
            SELECT cow_id, AVG(daily_total) AS avg_daily
              FROM daily_milk
             WHERE day < DATE('now', '-{HEALTH_RECENT_DAYS} days')
             GROUP BY cow_id
        ),
        kg_ranked AS (
            SELECT m.cow_id,
                   m.value,
                   ROW_NUMBER() OVER (PARTITION BY m.cow_id ORDER BY m.timestamp DESC) AS rn
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
             WHERE s.unit = 'kg'
        ),
        weight_current AS (
            SELECT cow_id, value AS current_kg
              FROM kg_ranked
             WHERE rn = 1
        ),
        weight_baseline AS (
            SELECT m.cow_id, AVG(m.value) AS avg_kg
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
             WHERE s.unit = 'kg'
               AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
               AND m.timestamp <  datetime('now', '-{HEALTH_RECENT_DAYS} days')
             GROUP BY m.cow_id
        )
        SELECT c.id,
               c.name,
               mr.avg_daily  AS milk_recent,
               mb.avg_daily  AS milk_baseline,
               wc.current_kg AS weight_current,
               wb.avg_kg     AS weight_baseline
          FROM cow c
          LEFT JOIN milk_recent    mr ON mr.cow_id = c.id
          LEFT JOIN milk_baseline  mb ON mb.cow_id = c.id
          LEFT JOIN weight_current wc ON wc.cow_id = c.id
          LEFT JOIN weight_baseline wb ON wb.cow_id = c.id
         WHERE mr.cow_id IS NOT NULL OR wc.cow_id IS NOT NULL
         ORDER BY c.name
        """
    ).fetchall()

    ill_cows: list[IllCowEntry] = []
    for cow_id, cow_name, milk_rec, milk_base, weight_cur, weight_base in rows:
        reasons = [
            r
            for r in [
                _assess_milk_health(milk_rec, milk_base),
                _assess_weight_health(weight_cur, weight_base),
            ]
            if r is not None
        ]
        if reasons:
            ill_cows.append(IllCowEntry(cow_id=cow_id, cow_name=cow_name, reasons=reasons))

    return ill_cows
