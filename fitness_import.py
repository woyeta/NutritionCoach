"""
Reads weight, height and daily steps from fitness-app export files, to fill in the profile.

Live connections aren't practical here: the Google Fit APIs are being shut down,
Health Connect (where Samsung Health syncs on Android) is only reachable from an Android
app, and Samsung Health has no web API. Both apps can export your data as files, so the
user uploads those instead:

- Samsung Health: Settings → Download personal data. Upload the ZIP, or the CSV files
  com.samsung.health.weight.*.csv, com.samsung.health.height.*.csv,
  com.samsung.shealth.tracker.pedometer_day_summary.*.csv and
  com.samsung.health.user_profile.*.csv (birth date, so age).
- Google Fit: Google Takeout → Fit. Upload "Daily activity metrics.csv".

Files are read in memory and never stored. Average daily steps are turned into an
activity level, which is an estimate: steps don't capture cycling, swimming or the gym.
"""

import csv
import io
import os
import statistics
import zipfile
from datetime import datetime, timedelta, timezone

MAX_FILE_BYTES = 50 * 1024 * 1024  # per CSV, including CSVs inside a ZIP
STEP_WINDOW_DAYS = 30

# Average daily steps → activity level (after Tudor-Locke and Bassett's step bands).
STEP_BANDS = [(5000, "sedentary"), (7500, "light"), (10000, "moderate"), (12500, "active")]

SUPPORTED_HELP = (
    "Upload a Samsung Health export (the ZIP, or its com.samsung.health.weight, "
    "com.samsung.health.height and com.samsung.shealth.tracker.pedometer_day_summary CSV files) "
    "or Google Takeout's Fit file \"Daily activity metrics.csv\"."
)


def activity_from_steps(avg_steps: float) -> str:
    for limit, level in STEP_BANDS:
        if avg_steps < limit:
            return level
    return "very_active"


# ── Reading files ──

def _expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Returns the CSV files, taking them out of any ZIPs."""
    out = []
    for name, data in files:
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.filename.lower().endswith(".csv") and info.file_size <= MAX_FILE_BYTES:
                            out.append((info.filename, zf.read(info)))
            except zipfile.BadZipFile:
                continue
        elif name.lower().endswith(".csv") and len(data) <= MAX_FILE_BYTES:
            out.append((name, data))
    return out


def _rows(data: bytes, expected: set) -> list[dict]:
    """
    Parses a CSV. Samsung files start with a metadata line and prefix every column
    (com.samsung.health.weight.weight), so the header is the first of the first three lines
    that has an expected column, and columns are matched on the name after the last ".".
    """
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    for start in range(min(3, len(lines))):
        header = next(csv.reader([lines[start]]), [])
        if any(h.strip().isdigit() for h in header):
            continue  # Samsung's metadata line, e.g. "com.samsung.health.weight,6313002,3"
        short = [h.strip().rsplit(".", 1)[-1] if h.strip().startswith("com.samsung") else h.strip()
                 for h in header]
        if expected & set(short):
            reader = csv.reader(lines[start + 1:])
            return [{k: v.strip() for k, v in zip(short, row) if k} for row in reader if any(row)]
    return []


def _time(value: str):
    """Epoch milliseconds or "YYYY-MM-DD[ HH:MM:SS[.fff]]" → datetime (UTC), or None."""
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:26], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _age(birth_date: str):
    """"YYYYMMDD" or "YYYY-MM-DD" → age in whole years, or None."""
    digits = (birth_date or "").replace("-", "").strip()[:8]
    try:
        born = datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if 1 <= age <= 120 else None


def _float(value):
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None


def _first_time(row: dict, keys=("start_time", "day_time", "create_time", "update_time")):
    return next((t for t in (_time(row.get(k)) for k in keys) if t), None)


# ── Collecting measurements ──

def _latest(readings: list):
    """readings: (time, value, source) → the most recent one."""
    dated = [r for r in readings if r[0] is not None]
    return max(dated, key=lambda r: r[0]) if dated else (readings[-1] if readings else None)


def import_files(files: list[tuple[str, bytes]]) -> dict:
    """
    files: (filename, bytes) pairs, CSV or ZIP. Returns
    {"profile": {weight_kg, height_cm, activity}, "details": [...], "sources": [...], "warnings": [...]},
    or {"error": "..."} if nothing usable was found.
    """
    weights, heights = [], []
    age, sex = None, None
    daily_steps = {}  # date → (steps, source)
    sources, warnings = set(), []

    for name, data in _expand(files):
        base = os.path.basename(name).lower()

        if base.startswith("com.samsung.health.weight."):
            for row in _rows(data, {"weight"}):
                t = _first_time(row)
                w, h = _float(row.get("weight")), _float(row.get("height"))
                if w:
                    weights.append((t, w, "Samsung Health"))
                if h:
                    heights.append((t, h, "Samsung Health"))
            sources.add("Samsung Health")

        elif base.startswith("com.samsung.health.user_profile."):
            # Key/value rows: key=birth_date, text_value=YYYYMMDD; key=gender if present.
            for row in _rows(data, {"key"}):
                key, text = row.get("key", ""), row.get("text_value", "").lower()
                if key == "birth_date":
                    age = _age(row.get("text_value", ""))
                elif key == "gender" and text in ("m", "male", "f", "female"):
                    sex = "male" if text.startswith("m") else "female"
            sources.add("Samsung Health")

        elif base.startswith("com.samsung.health.height."):
            for row in _rows(data, {"height"}):
                h = _float(row.get("height"))
                if h:
                    heights.append((_first_time(row), h, "Samsung Health"))
            sources.add("Samsung Health")

        elif base.startswith(("com.samsung.shealth.tracker.pedometer_day_summary.",
                              "com.samsung.shealth.step_daily_trend.")):
            for row in _rows(data, {"step_count", "count"}):
                steps = _float(row.get("step_count") or row.get("count"))
                t = _time(row.get("day_time")) or _first_time(row)
                if steps and t:
                    day = t.date()
                    # Phone and watch each report a total; keep the larger one.
                    if steps > daily_steps.get(day, (0, ""))[0]:
                        daily_steps[day] = (steps, "Samsung Health")
            sources.add("Samsung Health")

        else:
            rows = _rows(data, {"Step count", "Average weight (kg)"})
            if rows and "Date" in rows[0]:  # Google Takeout Fit "Daily activity metrics.csv"
                for row in rows:
                    t = _time(row.get("Date"))
                    w, steps = _float(row.get("Average weight (kg)")), _float(row.get("Step count"))
                    if w:
                        weights.append((t, w, "Google Fit"))
                    if steps and t and steps > daily_steps.get(t.date(), (0, ""))[0]:
                        daily_steps[t.date()] = (steps, "Google Fit")
                sources.add("Google Fit")

    profile, details = {}, []

    weight = _latest([r for r in weights if 20 <= r[1] <= 350])
    if weight:
        profile["weight_kg"] = round(weight[1], 1)
        when = f", measured {weight[0].date()}" if weight[0] else ""
        details.append(f"Weight {profile['weight_kg']:g} kg{when} ({weight[2]})")
        if weight[0] and (datetime.now(timezone.utc) - weight[0]).days > 90:
            warnings.append(f"The latest weight is from {weight[0].date()}; update it if it has changed.")

    heights = [(t, h * 100 if h < 3 else h, s) for t, h, s in heights]  # metres → cm
    height = _latest([r for r in heights if 100 <= r[1] <= 250])
    if height:
        profile["height_cm"] = round(height[1], 1)
        when = f", measured {height[0].date()}" if height[0] else ""
        details.append(f"Height {profile['height_cm']:g} cm{when} ({height[2]})")

    if age is not None:
        profile["age"] = age
        details.append(f"Age {age}, from your birth date (Samsung Health)")
    if sex:
        profile["sex"] = sex
        details.append(f"Sex: {sex} (Samsung Health)")

    today = datetime.now(timezone.utc).date()
    daily_steps = {d: v for d, v in daily_steps.items() if d < today}  # today isn't over yet
    if daily_steps:
        last_day = max(daily_steps)
        window = [s for d, (s, _) in daily_steps.items() if d > last_day - timedelta(days=STEP_WINDOW_DAYS)]
        avg = statistics.mean(window)
        profile["activity"] = activity_from_steps(avg)
        first_day = last_day - timedelta(days=STEP_WINDOW_DAYS - 1)
        details.append(f"Average {avg:,.0f} steps a day on {len(window)} days from {first_day} to {last_day}, "
                       f"so activity is set to \"{profile['activity']}\"")
        if len(window) < 7:
            warnings.append("Fewer than 7 days of steps, so the activity level is a rough guess.")
        warnings.append("Activity comes from steps only; choose a higher level yourself if you "
                        "also cycle, swim or train at the gym.")
        if (datetime.now(timezone.utc).date() - last_day).days > 60:
            warnings.append(f"The latest steps are from {last_day}, so they may be out of date.")

    if not profile:
        return {"error": "No weight, height or step data found in these files. " + SUPPORTED_HELP}
    return {"profile": profile, "details": details, "sources": sorted(sources), "warnings": warnings}
