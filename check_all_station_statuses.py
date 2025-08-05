#!/usr/bin/env python3
import os, json
from datetime import datetime, timedelta
from pymongo import MongoClient

# thresholds
STALE_THRESHOLD = timedelta(hours=1)
HUMIDITY_THRESH = 5.0
TEMP_THRESH     = 2.0
PRESSURE_THRESH = 5.0
PM_THRESH       = 5.0
CO2_THRESH      = 50.0

# special station collections
SPECIAL_STATIONS = {
    5463: ("f1_meteostation", "Timestamp"),
    100:  ("fidas_nyuad",     "datetime"),
    8394: ("buoy_01",         "datetime"),
}

def check_record_for_nulls(record):
    nulls = []
    def recurse(d, prefix=""):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if k in ("_id", "datetime", "Timestamp"):
                continue
            if (prefix=="" and k in ("lat","long")) or (prefix=="gps" and k=="position"):
                continue
            if v is None or v == "null":
                nulls.append(key)
            elif isinstance(v, dict):
                recurse(v, key)
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if item is None or item == "null":
                        nulls.append(f"{key}[{i}]")
                    elif isinstance(item, dict):
                        recurse(item, f"{key}[{i}]")
    recurse(record)
    return nulls

def get_status_report():
    # load config
    cfg_path = os.path.join(os.path.dirname(__file__), 'config', 'config.ini')
    cfg = {}
    from configparser import ConfigParser
    cp = ConfigParser()
    cp.read(cfg_path)
    uri    = cp.get('mongodb','uri')
    dbname = cp.get('mongodb','database')

    client = MongoClient(uri)
    db     = client[dbname]
    now    = datetime.utcnow()
    report = {}

    # load only configured stations
    stations_cfg = json.load(open('stations_to_check.json'))['stations']
    for sn in stations_cfg:
        coll_name, ts_field = SPECIAL_STATIONS.get(sn, (f"station{sn}", "datetime"))
        errs, rec_ts = [], None

        # fetch latest record
        try:
            rec = db[coll_name].find_one(sort=[(ts_field, -1)])
        except Exception as e:
            errs.append(f"Error accessing collection {coll_name}: {e}")
        else:
            if not rec:
                errs.append("No records found")
            else:
                rec_ts = rec.get(ts_field)
                if not isinstance(rec_ts, datetime):
                    errs.append(f"Missing/invalid '{ts_field}'")
                else:
                    age = now - rec_ts
                    if age > STALE_THRESHOLD:
                        mins = int(age.total_seconds() // 60)
                        errs.append(f"Stale data: {mins} minutes old")

                # null checks
                nulls = check_record_for_nulls(rec)
                if nulls:
                    errs.append("Null fields: " + ", ".join(nulls))

                # IoTBox drift checks
                info = db.stations_info.find_one({"station_num": sn}) or {}
                if info.get("type") == "IoTBox":
                    air = rec.get("air_sensor", [])
                    if isinstance(air, list) and len(air)>=2:
                        h0, h1 = air[0].get("humidity"), air[1].get("humidity")
                        if (h0 is None) ^ (h1 is None):
                            errs.append("Missing humidity on one sensor")
                        elif abs(h0-h1) > HUMIDITY_THRESH:
                            errs.append(f"Humidity diff {abs(h0-h1):.1f}% > {HUMIDITY_THRESH}%")
                        t0, t1 = air[0].get("temperature"), air[1].get("temperature")
                        if (t0 is None) ^ (t1 is None):
                            errs.append("Missing temperature on one sensor")
                        elif abs(t0-t1) > TEMP_THRESH:
                            errs.append(f"Temp diff {abs(t0-t1):.1f}°C > {TEMP_THRESH}°C")
                        p0, p1 = air[0].get("pressure"), air[1].get("pressure")
                        if (p0 is None) ^ (p1 is None):
                            errs.append("Missing pressure on one sensor")
                        elif abs(p0-p1) > PRESSURE_THRESH:
                            errs.append(f"Pressure diff {abs(p0-p1):.1f}hPa > {PRESSURE_THRESH}hPa")
                    pm = rec.get("particulate_matter", [])
                    if isinstance(pm, list) and len(pm)>=2:
                        if (pm[0] is None) ^ (pm[1] is None):
                            errs.append("Missing PM on one sensor")
                        elif abs(pm[0]-pm[1]) > PM_THRESH:
                            errs.append(f"PM diff {abs(pm[0]-pm[1]):.1f}µg/m³ > {PM_THRESH}µg/m³")
                    co2 = rec.get("co2_sensor", [])
                    if isinstance(co2, list) and len(co2)>=2:
                        c0 = co2[0].get("co2")
                        c1 = co2[1].get("co2")
                        if (c0 is None) ^ (c1 is None):
                            errs.append("Missing CO2 on one sensor")
                        elif abs(c0-c1) > CO2_THRESH:
                            errs.append(f"CO2 diff {abs(c0-c1):.1f}ppm > {CO2_THRESH}ppm")

        report[sn] = {
            "name": info.get("name", f"Station {sn}"),
            "timestamp": rec_ts,
            "errors": errs
        }

    client.close()
    return report
