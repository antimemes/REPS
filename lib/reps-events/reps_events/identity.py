"""Execution identity syntax; the launch-time label is not clock evidence."""

from typing import Annotated

from pydantic import Field

RUN_ID_PATTERN = r"^[0-9]{8}t[0-9]{6}z-[0-9a-f]{12}$"
RunId = Annotated[str, Field(pattern=RUN_ID_PATTERN)]
