import geopandas as gpd
import pandas as pd
import numpy as np
import plotly.express as px
import dash
from dash import dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc
import json
import os
from shapely.geometry import box
import tempfile
import requests
import zipfile
import glob

# === Define your available shapefile sources ===
shapefile_sources = {
    "test": "https://www.dropbox.com/scl/fi/7wxfgrlddf49lec66ltdc/test.zip?rlkey=wvh4g4oxkcym13789e5ean2ag&dl=1",
    "england": "https://www.dropbox.com/scl/fi/1wwyzlkalggbm7d5qn30a/england.zip?rlkey=lwcz15d3vsfohr2atzdqvs8rb&st=4nt52dnr&dl=1",
    "all": "https://www.dropbox.com/scl/fi/n3ea3x9d7zeqd2yrdbzlt/OECD_TL2_shapefile.zip?rlkey=5rmmxpqj4zskalmiva89h1zk1&st=e9qh17dw&dl=1"
}

shapefile_name = {
    "test": "test",
    "england": "england",
    "all": "OECD_TL2_shapefile"
}

region = "england"

def load_gdf_from_remote_zip(url, simplify_tolerance=0.005):
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_name = f"{region}.zip"
        zip_path = os.path.join(tmpdir, zip_name)

        r = requests.get(url)
        with open(zip_path, "wb") as f:
            f.write(r.content)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(tmpdir)

        shp_files = glob.glob(os.path.join(tmpdir, "**", "*.shp"), recursive=True)
        if shp_files:
            gdf = gpd.read_file(shp_files[0]).to_crs("EPSG:4326")
            gdf["ID"] = gdf.index.astype(str)
            if simplify_tolerance:
                gdf["geometry"] = gdf["geometry"].simplify(simplify_tolerance, preserve_topology=True)
            return gdf

    raise FileNotFoundError("Shapefile not found in ZIP")

# Load shapefile
gdf = load_gdf_from_remote_zip(shapefile_sources[region], simplify_tolerance=0.005)

# === Add extreme-value demo variables ===
def add_extreme_values(gdf, seed=42):
    np.random.seed(seed)
    n = len(gdf)
    gdf["ghg_emissions"] = np.round(
        np.where(np.random.rand(n) < 0.8,
                 np.random.normal(loc=5, scale=2, size=n),
                 np.random.normal(loc=1000, scale=100, size=n)), 2)
    gdf["pop_density"] = np.round(
        np.where(np.random.rand(n) < 0.8,
                 np.random.normal(loc=20, scale=5, size=n),
                 np.random.normal(loc=1200, scale=150, size=n)), 2)
    return gdf

os.chdir(os.path.dirname(os.path.abspath(__file__)))
gdf = add_extreme_values(gdf)
geojson = json.loads(gdf.to_json())
available_vars = ["ghg_emissions", "pop_density"]

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
server = app.server

app.layout = html.Div([
    dbc.Navbar(
        dbc.Container([
            dbc.Row([
                dbc.Col(html.H4("OECD Local Colour Scale Viewer", className="text-white fw-bold mb-0"), width="auto"),
            ], align="center", className="g-0"),
        ], fluid=True),
        color="dark", dark=True, className="mb-3 shadow-sm"
    ),
    dbc.Container([
        
        dbc.Row([
            dbc.Col([
                html.Label("Select Variable:", className="fw-bold small"),
                dcc.Dropdown(
                    id="var-dropdown",
                    options=[{"label": v.replace("_", " ").title(), "value": v} for v in available_vars],
                    value=available_vars[0],
                    style={"fontSize": "0.85rem"}
                )
            ], xs=12, sm=6, md=3),

            dbc.Col([
                html.Label("Local Colour Scale:", className="fw-bold small"),
                dcc.Checklist(
                    id="dynamic-scale",
                    options=[{"label": "Enable", "value": "dynamic"}],
                    value=[],
                    labelStyle={"marginRight": "1rem", "fontSize": "0.85rem"}
                )
            ], xs=12, sm=6, md=3),

            dbc.Col([
                html.Label("Lock Colour Scale:", className="fw-bold small"),
                dcc.Checklist(
                    id="lock-selection",
                    options=[{"label": "Lock", "value": "locked"}],
                    value=[],
                    labelStyle={"fontSize": "0.85rem"}
                )
            ], xs=12, sm=6, md=3),

            dbc.Col([
                html.Label("\u00a0", style={"display": "block"}),
                html.Button("Reset View", id="reset-view", n_clicks=0, className="btn btn-outline-secondary btn-sm")
            ], xs=12, sm=6, md=3)
        ])
    ], fluid=True),

    html.Div(style={"flex": "1"}, children=[
        dcc.Graph(
            id="map",
            style={"height": "85vh", "width": "100%"},
            config={"scrollZoom": True}
        )
    ]),

    dcc.Store(id="locked-colour-scale", data=None),
    dcc.Store(id="reset-trigger", data=0),
    dcc.Store(id="locked-bbox", data=None),

    html.Footer(
        dbc.Container(
            dbc.Row(
                dbc.Col(
                    html.Small("Built with Dash and Plotly | Local Scale Prototype", className="text-muted"),
                    className="text-center py-2"
                )
            )
        ),
        style={"backgroundColor": "#f8f9fa", "borderTop": "1px solid #dee2e6"}
    )
])

@app.callback(
    Output("map", "figure"),
    Input("var-dropdown", "value"),
    Input("dynamic-scale", "value"),
    Input("map", "relayoutData"),
    State("locked-colour-scale", "data"),
    State("lock-selection", "value"),
    State("locked-bbox", "data")
)
def update_map(var, dynamic, relayout_data, locked_colour, lock_selection, locked_bbox):
    ddf = gdf.copy()

    projected = gdf.to_crs("EPSG:3857")
    center_geom = projected.geometry.union_all().centroid
    center_point = gpd.GeoSeries([center_geom], crs="EPSG:3857").to_crs("EPSG:4326").iloc[0]
    center_lat = center_point.y
    center_lon = center_point.x
    zoom = 4

    if relayout_data:
        try:
            if "mapbox.center.lon" in relayout_data and "mapbox.center.lat" in relayout_data:
                center_lon = relayout_data["mapbox.center.lon"]
                center_lat = relayout_data["mapbox.center.lat"]
            elif "mapbox.center" in relayout_data:
                center_lat = relayout_data["mapbox.center"].get("lat", center_lat)
                center_lon = relayout_data["mapbox.center"].get("lon", center_lon)
            zoom = relayout_data.get("mapbox.zoom", zoom)
        except Exception as e:
            print("Viewport sync error:", e)

    zmin, zmax = None, None
    use_locked_colours = "locked" in lock_selection and locked_colour is not None

    if use_locked_colours and locked_bbox:
        bbox = box(
            locked_bbox["lon_min"],
            locked_bbox["lat_min"],
            locked_bbox["lon_max"],
            locked_bbox["lat_max"]
        )
        ddf = ddf[ddf.geometry.intersects(bbox)]
        if not ddf.empty:
            zmin = ddf[var].min()
            zmax = ddf[var].max()
        else:
            zmin = gdf[var].min()
            zmax = gdf[var].max()
    elif "dynamic" in dynamic:
        try:
            buffer_deg = max(0.05, 2.5 / (zoom + 0.5))
            lon_min = center_lon - buffer_deg
            lon_max = center_lon + buffer_deg
            lat_min = center_lat - buffer_deg
            lat_max = center_lat + buffer_deg
            bbox = box(lon_min, lat_min, lon_max, lat_max)
            ddf = ddf[ddf.geometry.intersects(bbox)]
            if not ddf.empty:
                zmin = ddf[var].min()
                zmax = ddf[var].max()
        except Exception as e:
            print("Bounding box error:", e)

    if zmin is None or zmax is None:
        zmin = gdf[var].min()
        zmax = gdf[var].max()

    print(f"{var=} {zmin=} {zmax=} visible_regions={len(ddf)}")

    fig = px.choropleth_mapbox(
        ddf,
        geojson=geojson,
        locations="ID",
        color=var,
        color_continuous_scale="Viridis",
        range_color=(zmin, zmax),
        hover_name="ID",
        center={"lat": center_lat, "lon": center_lon},
        mapbox_style="carto-positron",
        zoom=zoom,
        opacity=0.7
    )
    fig.update_layout(
        margin={"r": 0, "t": 20, "l": 0, "b": 0},
        font=dict(family="Segoe UI, Roboto, sans-serif", size=14, color="#333"),
        hoverlabel=dict(bgcolor="white", font_size=12, font_family="Segoe UI"),
        coloraxis_colorbar=dict(
            title=var.replace("_", " ").title(),
            orientation="h",
            yanchor="bottom",
            y=-0.12,
            x=0.5,
            xanchor="center",
            thickness=8,
            len=0.4
        )
    )
    return fig





@app.callback(
    Output("locked-colour-scale", "data"),
    Output("locked-bbox", "data"),
    Output("dynamic-scale", "value"),
    Output("lock-selection", "value"),
    Input("lock-selection", "value"),
    Input("reset-view", "n_clicks"),
    State("map", "relayoutData"),
    State("var-dropdown", "value")
)
def lock_colour_scale(lock_value, n_clicks, relayout_data, var):
    triggered_id = ctx.triggered_id
    if triggered_id == "reset-view":
        return None, None, [], []

    if "locked" not in lock_value or relayout_data is None:
        return None, None, dash.no_update, dash.no_update
    try:
        center = relayout_data.get("mapbox.center", {})
        center_lat = center.get("lat")
        center_lon = center.get("lon")
        zoom = relayout_data.get("mapbox.zoom", 4)
        buffer_deg = max(0.05, 2.5 / (zoom + 0.5))
        bbox = box(center_lon - buffer_deg, center_lat - buffer_deg,
                   center_lon + buffer_deg, center_lat + buffer_deg)
        visible = gdf[gdf.geometry.intersects(bbox)]
        if not visible.empty:
            return {
                "zmin": visible[var].min(),
                "zmax": visible[var].max()
            }, {
                "lat_min": center_lat - buffer_deg,
                "lat_max": center_lat + buffer_deg,
                "lon_min": center_lon - buffer_deg,
                "lon_max": center_lon + buffer_deg
            }, dash.no_update, dash.no_update
    except Exception as e:
        print("Lock error:", e)
    return None, None, dash.no_update, dash.no_update

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=True)
