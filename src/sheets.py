"""Google Sheets output: history (append), today, summary, compare, status.

Credentials come from the environment only:
  GOOGLE_CREDS       service-account JSON as a string (CI)
  GOOGLE_CREDS_PATH  path to the service-account JSON file (local)
  SHEET_ID           spreadsheet id (the part of the URL after /d/)

Every sheet has a note in row 1 (prices incl. VAT, excl. shipping; update
time; FX date), the header in row 2 and data from row 3.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import gspread

from src import tables
from src.tables import Column

log = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Europe/Warsaw")
SHEET_LOCALE = "en_GB"  # English UI, EU-style dates; the monitor owns this spreadsheet
DEFAULT_TAB_NAMES = {"Sheet1", "Лист1", "Arkusz1", "Tabellenblatt1", "Blad1", "List 1", "Feuille 1"}
# Locales that write function arguments with "," (the rest use ";" in formulas).
_COMMA_ARG_LOCALES = ("en", "ja", "zh", "ko", "th", "he", "iw", "hi", "ms", "fil")
HEADER_ROW, FIRST_DATA_ROW = 2, 3  # 1-based

HEADER_BG = {"red": 0.16, "green": 0.22, "blue": 0.33}
NOTE_FG = {"red": 0.4, "green": 0.4, "blue": 0.4}
GREEN_BG = {"red": 0.80, "green": 0.92, "blue": 0.80}
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}
AMBER_BG = {"red": 1.0, "green": 0.90, "blue": 0.70}
GREEN_FG = {"red": 0.12, "green": 0.50, "blue": 0.20}
RED_FG = {"red": 0.75, "green": 0.15, "blue": 0.15}

NUMBER_FORMATS = {
    "eur": {"type": "CURRENCY", "pattern": '#,##0.00 "€"'},
    "number": {"type": "NUMBER", "pattern": "#,##0"},  # counts; prices use "price"
    "price": {"type": "NUMBER", "pattern": "#,##0.00"},
    "pct": {"type": "PERCENT", "pattern": "0.0%"},
    "delta": {"type": "PERCENT", "pattern": "+0.0%;-0.0%;0.0%"},
}


def sheets_configured() -> bool:
    return bool(os.getenv("SHEET_ID") and (os.getenv("GOOGLE_CREDS") or os.getenv("GOOGLE_CREDS_PATH")))


def open_spreadsheet() -> gspread.Spreadsheet:
    if creds_json := os.getenv("GOOGLE_CREDS"):
        client = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        client = gspread.service_account(filename=os.environ["GOOGLE_CREDS_PATH"])
    return client.open_by_key(os.environ["SHEET_ID"])


class SheetsWriter:
    def __init__(self, spreadsheet: gspread.Spreadsheet, fx_date: str | None) -> None:
        self.book = spreadsheet
        self._prepare_book()
        updated = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")
        self.note = f"{tables.PRICE_NOTE} Updated {updated}. FX: ECB reference rates of {fx_date or 'n/a'}."

    def _prepare_book(self) -> None:
        """English locale + Warsaw time zone, and drop the empty default tab."""
        self.book.batch_update({"requests": [{"updateSpreadsheetProperties": {
            "properties": {"locale": SHEET_LOCALE, "timeZone": str(TIMEZONE)}, "fields": "locale,timeZone"}}]})
        meta = self.book.fetch_sheet_metadata({"fields": "properties.locale"})
        locale = meta["properties"]["locale"]
        self.arg_sep = "," if locale.split("_")[0] in _COMMA_ARG_LOCALES else ";"
        for ws in self.book.worksheets():
            if ws.title in DEFAULT_TAB_NAMES and len(self.book.worksheets()) > 1 and _is_empty(ws):
                self.book.del_worksheet(ws)

    # -- public ------------------------------------------------------------

    def write_all(self, products, fresh, statuses, today: date, write_history: bool = True) -> None:
        """products: today's dataset incl. carried-over stale offers; fresh: observed today."""
        history = self.append_history(fresh) if write_history else self.read_history()
        self.replace("compare", tables.COMPARE_COLUMNS, tables.compare_rows(products), self._compare_rules,
                     note_extra=tables.COMPARE_NOTE)
        self.replace("summary", tables.SUMMARY_COLUMNS, tables.summary_rows(products, history, today),
                     self._summary_rules)
        self.replace("today", tables.OFFER_COLUMNS, tables.offer_rows(products))
        self.replace("status", tables.STATUS_COLUMNS, tables.status_rows(statuses), self._status_rules)
        self._order_tabs(["compare", "summary", "today", "status", "history"])
        log.info("Google Sheet updated: %s", self.book.url)

    def read_history(self) -> list[dict[str, Any]]:
        try:
            ws = self.book.worksheet("history")
        except gspread.WorksheetNotFound:
            return []
        return self._records(ws, [c.header for c in tables.OFFER_COLUMNS])

    def append_history(self, fresh) -> list[dict[str, Any]]:
        """Append today's observed offers (never carried-over ones); return all history rows.

        Re-running on the same day replaces that day's rows, so history keeps
        exactly one snapshot per day.
        """
        headers = [c.header for c in tables.OFFER_COLUMNS]
        ws = self._worksheet("history", tables.OFFER_COLUMNS)
        current = ws.row_values(HEADER_ROW)
        if current != headers:
            if current and headers[: len(current)] == current:
                # new columns were appended at the end: extend the header, keep the rows
                log.info("history: adding columns %s", headers[len(current):])
                ws.resize(cols=len(headers))
                ws.update([headers], f"A{HEADER_ROW}", value_input_option="RAW")
                self.book.batch_update({"requests": self._base_format(ws.id, tables.OFFER_COLUMNS, ws.row_count - 2)})
            elif ws.row_values(FIRST_DATA_ROW):
                raise RuntimeError("history sheet has data with different columns — migrate it manually")
            else:
                self._reset(ws, tables.OFFER_COLUMNS, [])
        existing = self._records(ws, headers)
        today = fresh[0].date.isoformat() if fresh else None
        if fresh:
            same_day = [i for i, r in enumerate(existing) if r["Date"] == today]
            # Append first, then delete the old same-day rows above (Sheets refuses
            # to delete every non-frozen row; appended rows don't shift those indices).
            ws.append_rows(tables.offer_rows(fresh), value_input_option="RAW", table_range=f"A{HEADER_ROW}")
            if same_day:
                log.info("history: replaced %d rows already written for %s", len(same_day), today)
                self._delete_rows(ws, [i + FIRST_DATA_ROW - 1 for i in same_day])
            existing = self._records(ws, headers)
        last_row = len(existing) + HEADER_ROW
        self.book.batch_update({"requests": self._number_formats(ws.id, tables.OFFER_COLUMNS, last_row)})
        ws.update_acell("A1", self.note)
        return existing

    @staticmethod
    def _records(ws: gspread.Worksheet, headers: list[str]) -> list[dict[str, Any]]:
        """Rows as dicts with raw values (numbers as numbers, not "14.53 €")."""
        return ws.get_all_records(head=HEADER_ROW, expected_headers=headers,
                                  value_render_option=gspread.utils.ValueRenderOption.unformatted)

    def _delete_rows(self, ws: gspread.Worksheet, zero_based_rows: list[int]) -> None:
        """Delete rows, grouped into contiguous ranges, bottom-up so indices stay valid."""
        ranges: list[list[int]] = []
        for row in sorted(zero_based_rows):
            if ranges and row == ranges[-1][1]:
                ranges[-1][1] = row + 1
            else:
                ranges.append([row, row + 1])
        self.book.batch_update({"requests": [
            {"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": a, "endIndex": b}}}
            for a, b in reversed(ranges)]})

    def replace(self, title: str, columns: list[Column], rows: list[list[Any]], extra=None,
                note_extra: str = "") -> None:
        """Overwrite a sheet in place (keeps its URL/gid stable for bookmarks)."""
        ws = self._worksheet(title, columns)
        self._reset(ws, columns, rows, note_extra)
        self._write_links(ws, rows)
        if extra and rows:
            self.book.batch_update({"requests": extra(ws.id, columns, len(rows))})

    # -- layout ------------------------------------------------------------

    def _worksheet(self, title: str, columns: list[Column]) -> gspread.Worksheet:
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.book.add_worksheet(title, rows=100, cols=len(columns))

    def _reset(self, ws: gspread.Worksheet, columns: list[Column], rows: list[list[Any]],
               note_extra: str = "") -> None:
        """Rewrite a sheet from scratch: values, formats, widths, conditional rules."""
        self._clear_conditional_formats(ws)
        ws.clear()
        ws.resize(rows=max(len(rows) + FIRST_DATA_ROW, 10), cols=len(columns))
        note = f"{self.note} {note_extra}".strip()
        values = [[note], [c.header for c in columns], *[[tables.cell_value(c) for c in row] for row in rows]]
        ws.update(values, "A1", value_input_option="RAW")
        requests = self._base_format(ws.id, columns, len(rows)) + self._number_formats(ws.id, columns, len(rows) + 2)
        self.book.batch_update({"requests": requests})

    def _write_links(self, ws: gspread.Worksheet, rows: list[list[Any]]) -> None:
        """Turn Link cells into =HYPERLINK(url, value): still numeric, so formats and rules apply."""
        sep = self.arg_sep
        updates = []
        for r, row in enumerate(rows, start=FIRST_DATA_ROW):
            for c, cell in enumerate(row):
                if isinstance(cell, tables.Link):
                    number = f"{cell.value:.2f}"
                    if sep == ";":  # comma-decimal locales
                        number = number.replace(".", ",")
                    url = cell.url.replace('"', '""')
                    formula = f'=HYPERLINK("{url}"{sep}{number})'
                    updates.append({"range": f"{_col_letter(c)}{r}", "values": [[formula]]})
        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

    @staticmethod
    def _base_format(sheet_id: int, columns: list[Column], n_rows: int) -> list[dict]:
        n_cols = len(columns)
        requests: list[dict] = [
            {"repeatCell": {  # drop formatting left over from earlier runs
                "range": {"sheetId": sheet_id}, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}},
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": HEADER_ROW}},
                "fields": "gridProperties.frozenRowCount"}},
            {"unmergeCells": {"range": _rng(sheet_id, 0, 1, 0, n_cols)}},
            {"mergeCells": {"mergeType": "MERGE_ALL", "range": _rng(sheet_id, 0, 1, 0, n_cols)}},
            {"repeatCell": {
                "range": _rng(sheet_id, 0, 1, 0, n_cols),
                "cell": {"userEnteredFormat": {"textFormat": {"italic": True, "foregroundColor": NOTE_FG}}},
                "fields": "userEnteredFormat.textFormat"}},
            {"repeatCell": {
                "range": _rng(sheet_id, 1, 2, 0, n_cols),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": HEADER_BG, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
            {"setBasicFilter": {"filter": {"range": _rng(sheet_id, 1, n_rows + 2, 0, n_cols)}}},
        ]
        for i, col in enumerate(columns):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": col.width}, "fields": "pixelSize"}})
        return requests

    @staticmethod
    def _number_formats(sheet_id: int, columns: list[Column], last_row: int) -> list[dict]:
        return [
            {"repeatCell": {
                "range": _rng(sheet_id, 2, last_row, i, i + 1),
                "cell": {"userEnteredFormat": {"numberFormat": NUMBER_FORMATS[col.kind], "horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"}}
            for i, col in enumerate(columns) if col.kind in NUMBER_FORMATS and last_row > 2
        ]

    def _clear_conditional_formats(self, ws: gspread.Worksheet) -> None:
        meta = self.book.fetch_sheet_metadata({"fields": "sheets(properties.sheetId,conditionalFormats)"})
        sheet = next(s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id)
        count = len(sheet.get("conditionalFormats", []))
        if count:
            self.book.batch_update({"requests": [
                {"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}} for _ in range(count)]})

    def _order_tabs(self, order: list[str]) -> None:
        sheets = {ws.title: ws for ws in self.book.worksheets()}
        ordered = [sheets[t] for t in order if t in sheets] + [ws for t, ws in sheets.items() if t not in order]
        self.book.reorder_worksheets(ordered)

    # -- conditional formatting --------------------------------------------

    def _compare_rules(self, sheet_id: int, columns: list[Column], n_rows: int) -> list[dict]:
        first = tables.column_index(columns, tables.COUNTRIES[0])
        last = tables.column_index(columns, tables.COUNTRIES[-1])
        spread = tables.column_index(columns, "Spread")
        a, b = _col_letter(first), _col_letter(last)
        row = FIRST_DATA_ROW
        prices = _rng(sheet_id, 2, n_rows + 2, first, last + 1)
        s = self.arg_sep
        return [
            _formula_rule(prices, f"=AND(ISNUMBER({a}{row}){s}{a}{row}=MIN(${a}{row}:${b}{row}))", GREEN_BG, bold=True),
            _formula_rule(prices, f"=AND(ISNUMBER({a}{row}){s}{a}{row}=MAX(${a}{row}:${b}{row}){s}"
                                  f"COUNT(${a}{row}:${b}{row})>1)", RED_BG),
            _number_rule(_rng(sheet_id, 2, n_rows + 2, spread, spread + 1), "NUMBER_GREATER", "0.2", AMBER_BG),
        ]

    @staticmethod
    def _summary_rules(sheet_id: int, columns: list[Column], n_rows: int) -> list[dict]:
        rules = []
        for header in ("vs yesterday", "vs 7 days"):
            i = tables.column_index(columns, header)
            rng = _rng(sheet_id, 2, n_rows + 2, i, i + 1)
            rules.append(_number_rule(rng, "NUMBER_LESS", "0", None, fg=GREEN_FG))
            rules.append(_number_rule(rng, "NUMBER_GREATER", "0", None, fg=RED_FG))
        return rules

    @staticmethod
    def _status_rules(sheet_id: int, columns: list[Column], n_rows: int) -> list[dict]:
        i = tables.column_index(columns, "Status")
        rng = _rng(sheet_id, 2, n_rows + 2, i, i + 1)
        return [_text_rule(rng, "STALE", RED_BG), _text_rule(rng, "OK", GREEN_BG)]


def _is_empty(ws: gspread.Worksheet) -> bool:
    return not any(cell for row in ws.get_all_values() for cell in row)


# --- request helpers ----------------------------------------------------------------

def _rng(sheet_id: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _col_letter(index: int) -> str:
    return chr(ord("A") + index)  # every table has fewer than 26 columns


def _fmt(bg: dict | None, fg: dict | None = None, bold: bool = False) -> dict:
    fmt: dict = {}
    if bg:
        fmt["backgroundColor"] = bg
    text: dict = {}
    if fg:
        text["foregroundColor"] = fg
    if bold:
        text["bold"] = True
    if text:
        fmt["textFormat"] = text
    return fmt


def _rule(rng: dict, condition: dict, fmt: dict) -> dict:
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng], "booleanRule": {"condition": condition, "format": fmt}}}}


def _formula_rule(rng: dict, formula: str, bg: dict, bold: bool = False) -> dict:
    return _rule(rng, {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, _fmt(bg, bold=bold))


def _number_rule(rng: dict, kind: str, value: str, bg: dict | None, fg: dict | None = None) -> dict:
    return _rule(rng, {"type": kind, "values": [{"userEnteredValue": value}]}, _fmt(bg, fg, bold=bg is not None))


def _text_rule(rng: dict, text: str, bg: dict) -> dict:
    return _rule(rng, {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]}, _fmt(bg, bold=True))
