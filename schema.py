"""The extraction contract: what the LLM must return for each message."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WorkItem(BaseModel):
    task: str                          # e.g. "false ceiling"
    quantity: Optional[float] = None   # e.g. 40
    unit: Optional[str] = None         # normalized: "sq ft", "m", "nos", "kg"


class MaterialMove(BaseModel):
    material: str                      # normalized name: "cement"
    quantity: float
    unit: str                          # "bags", "kg", "nos", "m"
    direction: Literal["issued", "received"]
    note: Optional[str] = None         # supplier, challan no., who took it


class SiteUpdate(BaseModel):
    report_date: Optional[date] = None   # None -> use message date (today)
    work_items: list[WorkItem] = Field(default_factory=list)
    labour_count: Optional[int] = None
    materials: list[MaterialMove] = Field(default_factory=list)
    remarks: Optional[str] = None        # delays, issues, anything else
    needs_clarification: Optional[str] = None  # model asks if info is missing/ambiguous

    def is_empty(self) -> bool:
        return not (self.work_items or self.materials or self.labour_count or self.remarks)
