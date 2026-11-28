import io
import zipfile
from datetime import datetime, timedelta, timezone

from app import app
from fitness_import import activity_from_steps, import_files

TODAY = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def day(n):
    """n days ago, as a datetime."""
    return TODAY - timedelta(days=n)


def samsung_weight():
    rows = [f"{day(40):%Y-%m-%d} 07:00:00.000,70.2,172,UTC+0530,",
            f"{day(3):%Y-%m-%d} 07:10:00.000,68.4,,UTC+0530,"]
    return ("com.samsung.health.weight,6313002,3\n"
            "com.samsung.health.weight.start_time,com.samsung.health.weight.weight,"
            "com.samsung.health.weight.height,com.samsung.health.weight.time_offset,\n"
            + "\n".join(rows) + "\n").encode()


def samsung_height():
    return ("com.samsung.health.height,6313002,1\n"
            "com.samsung.health.height.start_time,com.samsung.health.height.height,\n"
            f"{day(10):%Y-%m-%d} 08:00:00.000,172.5,\n").encode()


def samsung_steps(per_day=8000, days=30):
    rows = []
    for n in range(1, days + 1):
        ms = int(day(n).timestamp() * 1000)
        rows.append(f"{ms},{per_day},phone,")
        rows.append(f"{ms},{per_day - 500},watch,")  # second device, lower total
    return ("com.samsung.shealth.tracker.pedometer_day_summary,6313001,9\n"
            "day_time,step_count,source_info,\n" + "\n".join(rows) + "\n").encode()


def google_daily_metrics():
    rows = [f"{day(n):%Y-%m-%d},30,{13000},{'71.0' if n == 2 else ''}" for n in range(1, 21)]
    return ("Date,Move Minutes count,Step count,Average weight (kg)\n" + "\n".join(rows) + "\n").encode()


def as_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buf.getvalue()


def test_activity_from_steps():
    assert [activity_from_steps(s) for s in (3000, 6000, 8000, 11000, 15000)] == [
        "sedentary", "light", "moderate", "active", "very_active"]


def test_samsung_csv_files():
    result = import_files([
        ("com.samsung.health.weight.20261127.csv", samsung_weight()),
        ("com.samsung.health.height.20261127.csv", samsung_height()),
        ("com.samsung.shealth.tracker.pedometer_day_summary.20261127.csv", samsung_steps()),
    ])
    assert result["profile"] == {"weight_kg": 68.4, "height_cm": 172.5, "activity": "moderate"}
    assert result["sources"] == ["Samsung Health"]
    assert "Average 8,000 steps a day on 30 days" in result["details"][2]  # phone total, not watch


def test_samsung_zip_export():
    folder = "samsunghealth_user_20261127/"
    data = as_zip([(folder + "com.samsung.health.weight.20261127.csv", samsung_weight()),
                   (folder + "com.samsung.shealth.tracker.pedometer_day_summary.20261127.csv", samsung_steps(4000)),
                   (folder + "jsons/something.json", b"{}")])
    result = import_files([("samsunghealth.zip", data)])
    assert result["profile"]["weight_kg"] == 68.4
    assert result["profile"]["activity"] == "sedentary"
    assert "height_cm" in result["profile"]  # from the weight file's height column (172)


def test_google_takeout_daily_metrics():
    result = import_files([("Daily activity metrics.csv", google_daily_metrics())])
    assert result["profile"] == {"weight_kg": 71.0, "activity": "very_active"}
    assert result["sources"] == ["Google Fit"]


def test_old_or_thin_data_gets_warnings():
    old = ("Date,Step count,Average weight (kg)\n"
           "2020-01-01,6000,80\n2020-01-02,6000,\n").encode()
    warnings = " ".join(import_files([("metrics.csv", old)])["warnings"])
    assert "Fewer than 7 days" in warnings and "out of date" in warnings and "latest weight is from 2020-01-01" in warnings


def test_unsupported_files():
    result = import_files([("notes.csv", b"a,b\n1,2\n"), ("photo.jpg", b"...")])
    assert "No weight, height or step data" in result["error"]


def test_import_endpoint():
    client = app.test_client()
    response = client.post("/api/profile/import", data={
        "files": [(io.BytesIO(samsung_weight()), "com.samsung.health.weight.1.csv"),
                  (io.BytesIO(samsung_steps()), "com.samsung.shealth.tracker.pedometer_day_summary.1.csv")],
    }, content_type="multipart/form-data")
    body = response.get_json()
    assert response.status_code == 200 and body["success"]
    assert body["profile"]["weight_kg"] == 68.4 and body["profile"]["activity"] == "moderate"
    assert client.post("/api/profile/import", data={}, content_type="multipart/form-data").status_code == 400
