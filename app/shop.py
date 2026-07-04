"""The closed world: everything Sarjy knows about the barbershop."""

from datetime import time
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.config import settings

TZ = ZoneInfo("Asia/Riyadh")

OPEN_TIME = time(10, 0)
CLOSE_TIME = time(22, 0)
CLOSED_WEEKDAY = 4  # Friday (Monday=0)


class Service(BaseModel):
    name: str
    minutes: int


SERVICES = [
    Service(name="haircut", minutes=30),
    Service(name="beard trim", minutes=15),
    Service(name="haircut and beard", minutes=45),
    Service(name="kids cut", minutes=20),
]

BARBERS = {
    "Ali": settings.calendar_id_ali,
    "Salem": settings.calendar_id_salem,
}


def menu_text() -> str:
    services = ", ".join(f"{s.name} ({s.minutes} min)" for s in SERVICES)
    return (
        f"Barbers: {', '.join(BARBERS)}. Services: {services}. "
        "Open Saturday to Thursday 10:00 to 22:00 Riyadh time, closed Friday."
    )
