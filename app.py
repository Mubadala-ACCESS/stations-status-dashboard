import os
import json
import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, callback_context, no_update
from dash.dependencies import Input, Output, State, ALL
import dash_leaflet as dl
from pymongo import MongoClient
import configparser
from datetime import datetime
import math

# import your existing status logic
from check_all_station_statuses import get_status_report

# load config
BASE_DIR = os.path.dirname(__file__)
cfg = configparser.ConfigParser()
cfg.read(os.path.join(BASE_DIR, 'config', 'config.ini'))
MONGO_URI         = cfg.get('mongodb', 'uri')
DB_NAME           = cfg.get('mongodb', 'database')
STATIONS_INFO_COL = cfg.get('mongodb', 'stations_info_collection')

# load station list
stations_file = os.path.join(BASE_DIR, 'stations_to_check.json')
with open(stations_file) as f:
    STATION_LIST = set(json.load(f)['stations'])

# init Dash
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP]
)
app.title = "Station Status Dashboard"
# serve assets/favicon.png automatically by referring just to its filename
app._favicon = "favicon.png"

# layout
app.layout = dbc.Container([
    dbc.NavbarSimple(
        brand=html.A(
            html.Img(src="/assets/maccess-logo.png", style={"height": "60px"}),
            href="/"
        ),
        color="purple", dark=True, style={"height": "80px"}
    ),

    dbc.Row([
        dbc.Col(
            dbc.Card(
                dbc.CardBody([
                    html.H4("Station Status", style={"fontWeight": "bold"}),
                    html.P("Updates every 5 minutes; click icon for details."),
                    dbc.Button("Refresh Now", id="refresh-btn", n_clicks=0, color="primary")
                ]),
                style={
                    "border": "2px solid purple",
                    "boxShadow": "2px 2px 5px lightgrey",
                    "height": "100%"
                }
            ), width=3
        ),
        dbc.Col(
            dbc.Card(
                dbc.CardBody(html.Div(id="map-div", style={"height": "100%"})),
                style={
                    "border": "2px solid purple",
                    "boxShadow": "2px 2px 5px lightgrey",
                    "height": "100%"
                }
            ), width=9
        )
    ], style={"height": "calc(100vh - 100px)", "alignItems": "stretch"}, className="gy-3"),

    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Station Status")),
        dbc.ModalBody(id="modal-body", style={"whiteSpace": "pre-wrap", "fontFamily": "monospace"}),
        dbc.ModalFooter(dbc.Button("Close", id="close-modal", className="ms-auto"))
    ], id="modal", is_open=False, size="lg", backdrop=True, scrollable=True),

    dcc.Interval(id="interval", interval=5*60*1000, n_intervals=0)
], fluid=True)


@app.callback(
    Output("map-div", "children"),
    Input("refresh-btn", "n_clicks"),
    Input("interval", "n_intervals")
)
def update_map(nc, ni):
    report = get_status_report()
    client = MongoClient(MONGO_URI)
    docs = list(client[DB_NAME][STATIONS_INFO_COL].find(
        {"lat": {"$ne": None}, "long": {"$ne": None}}
    ))
    client.close()

    # group by exact coordinates
    groups = {}
    for d in docs:
        try:
            sn = int(d.get("station_num"))
            lat = float(d["lat"])
            lon = float(d["long"])
        except:
            continue
        if sn not in STATION_LIST:
            continue
        groups.setdefault((lat, lon), []).append(sn)

    markers = []
    coords = []
    # jitter overlapping icons
    for (lat, lon), sns in groups.items():
        n = len(sns) or 1
        for idx, sn in enumerate(sns):
            angle = 2 * math.pi * idx / n
            r = 0.0005
            dlat = lat + r * math.sin(angle)
            dlon = lon + r * math.cos(angle)

            errs = report.get(sn, {}).get("errors", [])
            icon_file = "/assets/ok.png" if not errs else "/assets/problem.png"
            icon_dict = {
                "iconUrl": icon_file,
                "iconSize": [16, 16],
                "iconAnchor": [8, 8]  # center the smaller icon
            }

            markers.append(dl.Marker(
                position=(dlat, dlon),
                icon=icon_dict,
                id={"type": "marker", "station": sn},
                n_clicks=0
            ))
            coords.append((dlat, dlon))

    if not markers:
        return dl.Map(children=[dl.TileLayer()],
                      center=[24.53, 54.43], zoom=8,
                      style={"height": "100%", "width": "100%"})

    lats, lons = zip(*coords)
    bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

    return dl.Map(
        children=[dl.TileLayer(), dl.LayerGroup(markers)],
        bounds=bounds,
        style={"height": "100%", "width": "100%"}
    )


@app.callback(
    Output("modal", "is_open"),
    Output("modal-body", "children"),
    Input({"type": "marker", "station": ALL}, "n_clicks"),
    Input("close-modal", "n_clicks"),
    State("modal", "is_open")
)
def show_modal(marker_clicks, close_clicks, is_open):
    if close_clicks and is_open:
        return False, ""
    if not marker_clicks or sum(marker_clicks) == 0:
        return False, ""

    triggered = callback_context.triggered[0]["prop_id"]
    raw = triggered.split(".", 1)[0]
    sn = json.loads(raw)["station"]

    report = get_status_report().get(sn, {})
    name = report.get("name", f"Station {sn}")
    ts = report.get("timestamp")
    errs = report.get("errors", [])

    lines = []
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append("=" * 70)
    lines.append(f"Status At: {now_str}")
    lines.append("=" * 70)
    lines.append("")

    ok = not errs
    icon = "🟢 ✓" if ok else "🔴 ✗"
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S UTC") if ts else "N/A"

    lines.append(f"--- Station {sn}: {name} {icon} ---")
    lines.append(f"• Last timestamp : {ts_str}")
    if ok:
        lines.append("• Status         : OK")
    else:
        lines.append("• Issues:")
        for e in errs:
            if e.startswith("Null fields:"):
                fields = e.replace("Null fields: ", "").split(", ")
                lines.append("    – Null fields:")
                for f in fields:
                    lines.append(f"        • {f}")
            else:
                lines.append(f"    – {e}")
    lines.append("")
    lines.append("=" * 70)

    return True, "\n".join(lines)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=53456, debug=True)
