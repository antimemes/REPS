"""Run IDs use the launching machine's UTC clock and six random bytes."""

from datetime import datetime, timezone
from secrets import token_hex


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz-") + token_hex(6)
