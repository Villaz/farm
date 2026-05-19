"""Daily report endpoint."""

from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from api.dependencies import DbDep

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily", response_class=PlainTextResponse)
async def get_daily_report(db: DbDep, date: date | None = None) -> str:
    from api.report import ReportGenerator

    target = date or datetime.utcnow().date()
    return ReportGenerator().generate_from_connection(db, target)
