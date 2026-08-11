import hashlib
import re
from dataclasses import dataclass
from datetime import datetime as _dt

COLUMNS = [
    "שם לקוח", "תאריך", "עבודה", "שם חלקה", "גידול",
    "כמות", "שעות", "כלי", "מפעיל", "הערות", "מזין", "מזהה", "מזהה חלקה",
]
VALID_TASKS = {"חריש", "ריסוס", "קציר", "דיסוק", "אחר"}
_N_COLS = len(COLUMNS)  # 13 → column M


@dataclass
class WorkEntry:
    """Dataclass representing a validated agricultural work record.

    Includes deterministic UID generation (MD5 hash of entry content) to support
    deduplication during offline sync and multi-device operations.
    """

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
    uid:        str = ""
    field_uid:  str = ""

    def __post_init__(self):
        """Validate required fields and compute deterministic UID if not present.

        Raises:
            ValueError: If client is empty, date is not in YYYY-MM-DD format,
                        date is invalid, or task is not in VALID_TASKS.
        """
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

        if not self.uid or not self.uid.strip():
            raw = f"{self.client}|{self.date}|{self.task}|{self.field_name}|{self.amount}|{self.operator}"
            self.uid = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
        else:
            self.uid = self.uid.strip()

    def to_sheet_row(self) -> list:
        """Convert entry into an ordered list matching COLUMNS for Google Sheets export.

        Returns:
            list: List of string values corresponding to COLUMNS headers.
        """
        return [
            self.client, self.date, self.task, self.field_name,
            self.crop, self.amount, self.hours,
            self.tool, self.operator, self.notes, self.entered_by,
            self.uid, self.field_uid,
        ]

    def to_dict(self) -> dict:
        """Convert entry into a dictionary keyed by Hebrew COLUMNS headers.

        Returns:
            dict: Mapping of COLUMNS header names to entry field values.
        """
        return dict(zip(COLUMNS, self.to_sheet_row()))

    @classmethod
    def from_dict(cls, d: dict) -> "WorkEntry":
        """Instantiate a WorkEntry from a dictionary keyed by Hebrew COLUMNS.

        Args:
            d (dict): Dictionary mapping column names to entry values.

        Returns:
            WorkEntry: Validated WorkEntry instance.
        """
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
            uid=str(d.get("מזהה", "")),
            field_uid=str(d.get("מזהה חלקה", "")),
        )

    @classmethod
    def from_form(cls, form, entered_by: str = "Web") -> "WorkEntry":
        """Instantiate a WorkEntry from a Flask request form dictionary.

        Args:
            form (dict): Form request data mapping Hebrew column keys.
            entered_by (str): Name or role of the user submitting the form.

        Returns:
            WorkEntry: Validated WorkEntry instance.
        """
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
            uid=form.get("מזהה", ""),
            field_uid=form.get("מזהה חלקה", ""),
        )

    @classmethod
    def from_bot(cls, user_data: dict, full_name: str) -> "WorkEntry":
        """Instantiate a WorkEntry from Telegram bot conversation user_data.

        Args:
            user_data (dict): State dictionary collected during Telegram bot conversation.
            full_name (str): Full name of the Telegram user submitting the entry.

        Returns:
            WorkEntry: Validated WorkEntry instance.
        """
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
            uid=user_data.get("מזהה", ""),
            field_uid=user_data.get("מזהה חלקה", ""),
        )
