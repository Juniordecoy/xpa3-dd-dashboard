from __future__ import annotations

from flask import Flask, render_template, request, redirect, url_for, send_file, Response
from datetime import datetime, timezone
import os
import csv
import io

# --- Timezone (DST-safe when tzdata is available) ---
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    EASTERN = ZoneInfo("America/New_York")
except Exception:
    EASTERN = None  # fallback if tzdata missing on Windows

def now_str(with_tz_label: bool = False) -> str:
    if EASTERN:
        s = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
        return f"{s} ET" if with_tz_label else s
    # fallback so app never crashes
    s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return f"{s} UTC" if with_tz_label else s


app = Flask(__name__)

# Local CSV log (handy locally; NOT reliable on Render free tier disk)
CSV_LOG_PATH = "door_state_log.csv"


# ---- DB (Render Postgres) ----
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None
    RealDictCursor = None


def get_db_conn():
    """Return a psycopg2 connection if DATABASE_URL is set and psycopg2 is installed."""
    if psycopg2 is None:
        return None
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    # Render sometimes provides postgres:// but psycopg2 expects postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url)


def init_db_and_seed():
    """Create table and seed it with current in-memory defaults if empty."""
    conn = get_db_conn()
    if not conn:
        return

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS door_state (
                    door INTEGER PRIMARY KEY,
                    location TEXT,
                    truck_type TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                """
            )
            # seed if empty (so a cold start has persistent state)
            cur.execute("SELECT COUNT(*) FROM door_state;")
            count = cur.fetchone()[0]
            if count == 0:
                for d, loc in all_doors().items():
                    cur.execute(
                        """
                        INSERT INTO door_state (door, location, truck_type, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (door) DO NOTHING;
                        """,
                        (d, loc, door_truck_type.get(d)),
                    )
    conn.close()


def load_state_from_db():
    conn = get_db_conn()
    if not conn:
        return False

    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT door, location, truck_type FROM door_state;")
            rows = cur.fetchall()
    conn.close()

    if not rows:
        return True  # DB reachable, but empty (seed happens in init)

    for r in rows:
        d = int(r["door"])
        loc = r["location"] or "—"
        trk = (r.get("truck_type") or "").strip().upper() or None

        if d in front:
            front[d] = loc
        elif d in back:
            back[d] = loc

        if trk:
            door_truck_type[d] = trk
        else:
            door_truck_type.pop(d, None)

    return True


def save_door_to_db(door_num: int, location: str, truck_type: str | None):
    conn = get_db_conn()
    if not conn:
        return
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO door_state (door, location, truck_type, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (door) DO UPDATE SET
                    location = EXCLUDED.location,
                    truck_type = EXCLUDED.truck_type,
                    updated_at = NOW();
                """,
                (door_num, location, truck_type),
            )
    conn.close()


# ---- DATA (your board) ----
front = {
    1: "IND9",
    2: "XNJ2",
    3: "IB",
    4: "IB",
    5: "IB",
    6: "IB",
    7: "IB",
    8: "XMD2",
    9: "IB",
    10: "IB",
    11: "XPH7",
    12: "XLA3",
    13: "TEB9",
    14: "XCE1",
    15: "RDU2",
}

back = {
    122: "ABE8",
    123: "XME1",
    124: "SMF3",
    125: "VGT2",
    126: "AVP1",
    127: "—",   # blank spot placeholder
    128: "PHL4",
    129: "XCL1",
    130: "CHO1",
    131: "XAT3",
    132: "SWF2",
    133: "SCK4",
    134: "RMN3",
    135: "CMH3",
    136: "GYR3",
    137: "FTW1",
    138: "—",   # blank spot placeholder
    139: "HIA1",
}

truck_by_location = {
    "XME1": "JBHU",
    "SCK4": "JBHU",
    "XAT3": "AZNU",
    "FTW1": "JBHU",
    "VGT2": "XPOU",
    "XLA3": "JBHU",
    "GYR3": "JBHU",
    "SMF3": "JBHU",
    "CLOSED": "",
    "Empty Door": "",
}

DEFAULT_TRUCK = "AZNG"

# Door override truck types: {door_number: "JBHU"/"XPOU"/"AZNU"/"AZNG"}
door_truck_type: dict[int, str] = {}

TRUCK_TYPES = ["AZNG", "AZNU", "JBHU", "XPOU"]

ALL_LOCATIONS = sorted({
    # Add every location code you want available in the dropdown:
    "ABE8", "AVP1", "CHO1", "CMH3", "FTW1", "GYR3", "HIA1",
    "IND9", "PHL4", "RMN3", "SCK4", "SMF3", "SWF2",
    "VGT2", "XAT3", "XCE1", "XCL1", "XLA3", "XMD2",
    "XME1", "XNJ2", "XPH7", "RDU2", "TEB9", "TEB4",
    "MEM1", "XLX1", "XRD4", "LAS1", "FWA4", "MDW2",
    "XMI3", "CLT2", "HGR6", "ABE4", "XRI3", "BOS7",
    "IB", "CLOSED", "Empty", "Empty Door",
})


# ---- CSV LOG HELPERS (optional) ----
def append_update_to_csv(door: int, location: str, truck_type: str | None):
    """Append-only log. Handy locally; on Render free tier it may reset on restart."""
    file_exists = os.path.exists(CSV_LOG_PATH)

    with open(CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["door", "location", "truck_type", "updated_at"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "door": door,
            "location": location,
            "truck_type": truck_type or "",
            "updated_at": now_str(with_tz_label=False),
        })


def normalize_loc(loc: str) -> str:
    return (loc or "").strip().upper()


def is_blank_loc(loc: str) -> bool:
    loc = (loc or "").strip()
    # treat empty or non-alphanumeric as blank (—, ---, etc)
    return (loc == "") or (not loc.replace(" ", "").isalnum())


def get_truck_for_door(door_num: int, loc: str) -> str:
    if is_blank_loc(loc):
        return ""
    if door_num in door_truck_type:
        return door_truck_type[door_num]
    return truck_by_location.get(normalize_loc(loc), DEFAULT_TRUCK)


def all_doors() -> dict[int, str]:
    combined: dict[int, str] = {}
    combined.update(front)
    combined.update(back)
    return combined


def build_location_options():
    locs = set()

    for loc in front.values():
        loc = normalize_loc(loc)
        if loc.isalnum():
            locs.add(loc)

    for loc in back.values():
        loc = normalize_loc(loc)
        if loc.isalnum():
            locs.add(loc)

    for loc in truck_by_location.keys():
        locs.add(normalize_loc(loc))

    return sorted(locs)


# ---- Startup: init DB + load state ----
# DB is the source of truth on Render. If DB is unavailable, app still runs with defaults.
init_db_and_seed()
load_state_from_db()


# ---- ROUTES ----
@app.get("/")
def index():
    ts = now_str(with_tz_label=True)

    front_rows = [(d, front[d], get_truck_for_door(d, front[d])) for d in sorted(front)]
    back_rows = [(d, back[d], get_truck_for_door(d, back[d])) for d in sorted(back)]

    return render_template(
        "index.html",
        timestamp=ts,
        front_rows=front_rows,
        back_rows=back_rows,
        truck_types=TRUCK_TYPES,
        overrides=door_truck_type,
        location_options=ALL_LOCATIONS,  # use master list
        door_options=sorted(all_doors().keys()),
    )


@app.post("/update-location")
def update_location():
    door = request.form.get("door", "").strip()
    loc = normalize_loc(request.form.get("location", ""))

    if not door.isdigit():
        return redirect(url_for("index"))

    door_num = int(door)
    doors = all_doors()

    if door_num not in doors:
        return redirect(url_for("index"))

    if loc in ["", "—", "---", "----"]:
        loc = "—"

    if door_num in front:
        front[door_num] = loc
    else:
        back[door_num] = loc

    if is_blank_loc(loc) and door_num in door_truck_type:
        door_truck_type.pop(door_num, None)

    # Persist
    save_door_to_db(door_num, loc, door_truck_type.get(door_num))
    append_update_to_csv(door_num, loc, door_truck_type.get(door_num))

    return redirect(url_for("index"))


@app.post("/override-truck")
def override_truck():
    door = request.form.get("door", "").strip()
    truck = request.form.get("truck", "").strip().upper()

    if not door.isdigit():
        return redirect(url_for("index"))

    door_num = int(door)
    if door_num not in all_doors():
        return redirect(url_for("index"))

    loc = all_doors()[door_num]

    # If door is blank, don't set override
    if is_blank_loc(loc):
        door_truck_type.pop(door_num, None)
        save_door_to_db(door_num, loc, None)
        append_update_to_csv(door_num, loc, None)
        return redirect(url_for("index"))

    # Clear override option
    if truck == "AUTO":
        door_truck_type.pop(door_num, None)
        save_door_to_db(door_num, loc, None)
        append_update_to_csv(door_num, loc, None)
        return redirect(url_for("index"))

    if truck in TRUCK_TYPES:
        door_truck_type[door_num] = truck
        save_door_to_db(door_num, loc, truck)
        append_update_to_csv(door_num, loc, truck)

    return redirect(url_for("index"))


@app.get("/download-csv")
def download_csv():
    """Download a snapshot CSV of current state.

    If Postgres is available, export from DB (survives Render sleeps).
    Otherwise, fall back to the local CSV log file.
    """
    conn = get_db_conn()
    if conn:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT door, location, truck_type, updated_at FROM door_state ORDER BY door;")
                rows = cur.fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["door", "location", "truck_type", "updated_at"])
        for r in rows:
            writer.writerow([r["door"], r["location"], r["truck_type"] or "", r["updated_at"]])

        csv_bytes = output.getvalue().encode("utf-8")
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=door_state_snapshot.csv"},
        )

    # fallback: download local log file if present
    if not os.path.exists(CSV_LOG_PATH):
        with open(CSV_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("door,location,truck_type,updated_at\n")

    return send_file(
        CSV_LOG_PATH,
        as_attachment=True,
        download_name=os.path.basename(CSV_LOG_PATH),
        mimetype="text/csv",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
