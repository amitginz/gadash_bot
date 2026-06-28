import re
from dataclasses import dataclass
from datetime import datetime as _dt

COLUMNS = [
    "שם לקוח", "תאריך", "עבודה", "שם חלקה", "גידול",
    "כמות", "שעות", "כלי", "מפעיל", "הערות", "מזין",
]
VALID_TASKS = {"חריש", "ריסוס", "קציר", "דיסוק", "אחר"}
_N_COLS = len(COLUMNS)  # 11 → column K


@dataclass
class WorkEntry:
    client:     str
    date:       str
    task:       str
    field_name: str = ""
    crop:       str = ""
    amount:     str = ""
    hours:      str = ""
    tool:       str = ""
    operator:   str = ""
    notes:      str = ""
    entered_by: str = ""

    def __post_init__(self):
        self.client = self.client.strip()
        self.date   = self.date.strip()
        self.task   = self.task.strip()
        if not self.client:
            raise ValueError("שם לקוח חובה")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.date):
            raise ValueError(f"תאריך לא תקין: '{self.date}' — נדרש YYYY-MM-DD")
        try:
            _dt.strptime(self.date, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"תאריך לא קיים: '{self.date}'")
        if self.task not in VALID_TASKS:
            raise ValueError(f"סוג עבודה לא תקין: '{self.task}'")

    def to_sheet_row(self) -> list:
        return [
            self.client, self.date, self.task, self.field_name,
            self.crop, self.amount, self.hours,
            self.tool, self.operator, self.notes, self.entered_by,
        ]

    def to_dict(self) -> dict:
        return dict(zip(COLUMNS, self.to_sheet_row()))

    @classmethod
    def from_dict(cls, d: dict) -> "WorkEntry":
        return cls(
            client=str(d.get("שם לקוח", "")),
            date=str(d.get("תאריך", "")),
            task=str(d.get("עבודה", "")),
            field_name=str(d.get("שם חלקה", "")),
            crop=str(d.get("גידול", "")),
            amount=str(d.get("כמות", "")),
            hours=str(d.get("שעות", "")),
            tool=str(d.get("כלי", "")),
            operator=str(d.get("מפעיל", "")),
            notes=str(d.get("הערות", "")),
            entered_by=str(d.get("מזין", "")),
        )

    @classmethod
    def from_form(cls, form, entered_by: str = "Web") -> "WorkEntry":
        return cls(
            client=form.get("שם לקוח", ""),
            date=form.get("תאריך", ""),
            task=form.get("עבודה", ""),
            field_name=form.get("שם חלקה", ""),
            crop=form.get("גידול", ""),
            amount=form.get("כמות", ""),
            hours=form.get("שעות", ""),
            tool=form.get("כלי", ""),
            operator=form.get("מפעיל", ""),
            notes=form.get("הערות", ""),
            entered_by=entered_by,
        )

    @classmethod
    def from_bot(cls, user_data: dict, full_name: str) -> "WorkEntry":
        return cls(
            client=user_data.get("שם לקוח", ""),
            date=user_data.get("תאריך", ""),
            task=user_data.get("עבודה", ""),
            field_name=user_data.get("שם חלקה", ""),
            crop=user_data.get("גידול", ""),
            amount=user_data.get("כמות", ""),
            hours=user_data.get("שעות", ""),
            tool=user_data.get("כלי", ""),
            operator=user_data.get("מפעיל", ""),
            notes=user_data.get("הערות", ""),
            entered_by=full_name,
        )
