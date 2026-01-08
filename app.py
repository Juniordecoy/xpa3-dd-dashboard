from flask import Flask, render_template, request, redirect, url_for, send_file
from datetime import datetime
import os
import csv

app = Flask(__name__)
CSV_LOG_PATH = "door_state_log.csv"

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
door_truck_type = {}

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

# ---- HELPERS ----
def append_update_to_csv(door: int, location: str, truck_type: str | None):
    file_exists = os.path.exists(CSV_LOG_PATH)

    with open(CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["door", "location", "truck_type", "updated_at"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        # write header once
        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "door": door,
            "location": location,
            "truck_type": truck_type or "",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

def load_state_from_csv_log():
    if not os.path.exists(CSV_LOG_PATH):
        return

    with open(CSV_LOG_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = int(row["door"])
            loc = row["location"] or "—"
            trk = (row.get("truck_type") or "").strip().upper() or None

            if d in front:
                front[d] = loc
            elif d in back:
                back[d] = loc

            if trk:
                door_truck_type[d] = trk
            else:
                door_truck_type.pop(d, None)

def normalize_loc(loc: str) -> str:
    return (loc or "").strip().upper()

def is_blank_loc(loc: str) -> bool:
    loc = normalize_loc(loc)
    # treat anything non-alphanumeric as blank (—, ---, etc)
    return (loc == "") or (not loc.isalnum())

def get_truck_for_door(door_num: int, loc: str) -> str:
    # If blank spot, no truck
    if is_blank_loc(loc):
        return ""
    # If door has a forced override, use it
    if door_num in door_truck_type:
        return door_truck_type[door_num]
    # Else determine by location, fallback default
    return truck_by_location.get(normalize_loc(loc), DEFAULT_TRUCK)

def all_doors():
    # combine for easy lookup/validation
    combined = {}
    combined.update(front)
    combined.update(back)
    return combined

def build_location_options():
    # Collect from current board + from your truck map (so you can pick even if not currently on board)
    locs = set()

    for loc in front.values():
        loc = normalize_loc(loc)
        if loc.isalnum():
            locs.add(loc)

    for loc in back.values():
        loc = normalize_loc(loc)
        if loc.isalnum():
            locs.add(loc)

    # Also include anything you've already defined in truck_by_location
    for loc in truck_by_location.keys():
        locs.add(normalize_loc(loc))

    return sorted(locs)

load_state_from_csv_log()

# ---- ROUTES ----
@app.get("/")
def index():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    front_rows = [(d, front[d], get_truck_for_door(d, front[d])) for d in sorted(front)]
    back_rows = [(d, back[d], get_truck_for_door(d, back[d])) for d in sorted(back)]

    location_options = build_location_options()

    return render_template(
        "index.html",
        timestamp=ts,
        front_rows=front_rows,
        back_rows=back_rows,
        truck_types=TRUCK_TYPES,
        overrides=door_truck_type,
        location_options=ALL_LOCATIONS,  # ✅ use master list
        door_options=sorted(all_doors().keys()),
    )

@app.post("/update-location")
def update_location():
    door = request.form.get("door", "").strip()
    loc = normalize_loc(request.form.get("location", ""))

    # Basic safety: door must be a number and exist
    if not door.isdigit():
        return redirect(url_for("index"))

    door_num = int(door)
    doors = all_doors()

    if door_num not in doors:
        return redirect(url_for("index"))

    # If user wants blank, allow "" or "—"
    if loc in ["", "—", "---", "----"]:
        loc = "—"

    # Update the correct side
    if door_num in front:
        front[door_num] = loc
    else:
        back[door_num] = loc

    # Optional: if it becomes blank, remove any forced override
    if is_blank_loc(loc) and door_num in door_truck_type:
        door_truck_type.pop(door_num, None)

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

    # If door is blank, don't set override
    loc = all_doors()[door_num]
    if is_blank_loc(loc):
        door_truck_type.pop(door_num, None)
        append_update_to_csv(door_num, loc, None)
        return redirect(url_for("index"))

    # Clear override option
    if truck == "AUTO":
        door_truck_type.pop(door_num, None)
        append_update_to_csv(door_num, loc, None)
        return redirect(url_for("index"))

    if truck in TRUCK_TYPES:
        door_truck_type[door_num] = truck
        append_update_to_csv(door_num, loc, None)
    return redirect(url_for("index"))

@app.get("/download-csv")
def download_csv():
    path = CSV_LOG_PATH  # or "door_state_log.csv"
    if not os.path.exists(path):
        # optional: create an empty file so download still works
        with open(path, "w", encoding="utf-8") as f:
            f.write("door,location,truck_type,updated_at\n")

    return send_file(
        path,
        as_attachment=True,
        download_name=os.path.basename(path),
        mimetype="text/csv",
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)