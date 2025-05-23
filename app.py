import geopandas as gpd
import pandas as pd
import numpy as np
import plotly.express as px
import dash
from dash import dcc, html, Input, Output, State, ctx
import json
import os
from shapely.geometry import box

# === Load and simplify shapefile ===
def load_gdf(path, simplify_tolerance=0.005):
    gdf = gpd.read_file(path).to_crs("EPSG:4326")
    gdf["ID"] = gdf.index.astype(str)
    if simplify_tolerance:
        gdf["geometry"] = gdf["geometry"].simplify(simplify_tolerance, preserve_topology=True)
    return gdf

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

# Load data
os.chdir(os.path.dirname(os.path.abspath(__file__)))
gdf = load_gdf("data/OECD_TL2_2020.shp")
gdf = add_extreme_values(gdf)
geojson = json.loads(gdf.to_json())
available_vars = ["ghg_emissions", "pop_density"]

# === Dash App ===
app = dash.Dash(__name__)
server = app.server

app.layout = html.Div(
    style={
        "height": "100vh",
        "display": "flex",
        "flexDirection": "column",
        "fontFamily": "Segoe UI, Roboto, sans-serif",
        "backgroundColor": "#ffffff"
    },
    children=[
        html.Div(
            style={
                "padding": "1.5rem 2rem",
                "background": "#ffffff",
                "borderBottom": "1px solid #d3d3d3",
                "borderBottom": "1px solid #d3d3d3",
                "textAlign": "center"
            },
            children=[
                html.H2("Local Colour Scale Demonstration", style={"marginBottom": "1rem"}),
                html.Div(
                    style={
                        "display": "flex",
                        "justifyContent": "center",
                        "alignItems": "center",
                        "flexWrap": "wrap",
                        "gap": "1rem"
                    },
                    children=[
                        html.Div([
                            html.Label("Select Variable:", style={"fontWeight": "bold"}),
                            dcc.Dropdown(
                                id="var-dropdown",
                                options=[{"label": v.replace("_", " ").title(), "value": v} for v in available_vars],
                                value=available_vars[0],
                                style={"width": "200px"}
                            )
                        ]),
                        html.Div([
                            html.Label("Local Colour Scale:", style={"fontWeight": "bold"}),
                            dcc.Checklist(
                                id="dynamic-scale",
                                options=[{"label": "Enable", "value": "dynamic"}],
                                value=[],
                                labelStyle={"marginRight": "1rem"}
                            )
                        ]),
                        html.Div([
                            html.Label("Lock Colour Scale:", style={"fontWeight": "bold"}),
                            dcc.Checklist(
                                id="lock-selection",
                                options=[{"label": "Lock", "value": "locked"}],
                                value=[],
                                inline=True
                            )
                        ]),
                        html.Button("Reset View", id="reset-view", n_clicks=0)
                    ]
                )
            ]
        ),
        html.Div(
            style={"flex": "1"},
            children=[
                dcc.Graph(
                    id="map",
                    style={"height": "100%", "width": "100%"},
                    config={"scrollZoom": True}
                )
            ]
        ),
        dcc.Store(id="locked-colour-scale", data=None),
        dcc.Store(id="reset-trigger", data=0)
    ]
)

@app.callback(
    Output("dynamic-scale", "value"),
    Output("lock-selection", "value"),
    Output("locked-colour-scale", "data"),
    Output("reset-trigger", "data"),
    Input("reset-view", "n_clicks"),
    Input("lock-selection", "value"),
    State("map", "relayoutData"),
    State("var-dropdown", "value"),
    State("reset-trigger", "data"),
    prevent_initial_call=True
)
def unified_control_reset(reset_clicks, lock_value, relayout_data, var, prev_reset_flag):
    triggered_id = ctx.triggered_id

    # Handle reset
    if triggered_id == "reset-view":
        return [], [], None, reset_clicks

    # Handle lock toggle — when checklist was ticked on
    if "locked" in lock_value and relayout_data:
        try:
            if "mapbox.center.lon" in relayout_data and "mapbox.center.lat" in relayout_data:
                center_lon = relayout_data["mapbox.center.lon"]
                center_lat = relayout_data["mapbox.center.lat"]
            elif "mapbox.center" in relayout_data:
                center = relayout_data["mapbox.center"]
                center_lat = center.get("lat")
                center_lon = center.get("lon")
            else:
                return dash.no_update, dash.no_update, dash.no_update, dash.no_update

            zoom = relayout_data.get("mapbox.zoom", 4)
            buffer_deg = max(2, 15 / (zoom + 0.5))
            lon_min = center_lon - buffer_deg
            lon_max = center_lon + buffer_deg
            lat_min = center_lat - buffer_deg
            lat_max = center_lat + buffer_deg
            bbox = box(lon_min, lat_min, lon_max, lat_max)
            visible = gdf[gdf.geometry.intersects(bbox)]

            if not visible.empty:
                locked = {
                    "zmin": visible[var].min(),
                    "zmax": visible[var].max()
                }
                return dash.no_update, dash.no_update, locked, dash.no_update
        except Exception as e:
            print("Colour lock capture failed:", e)

    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


@app.callback(
    Output("map", "figure"),
    Input("var-dropdown", "value"),
    Input("dynamic-scale", "value"),
    Input("map", "relayoutData"),
    Input("reset-trigger", "data"),
    State("map", "figure"),
    State("locked-colour-scale", "data"),
    State("lock-selection", "value")
)
def update_map(var, dynamic, relayout_data, reset_flag, figure_state, locked_colour, lock_selection):
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

    if use_locked_colours:
        zmin = locked_colour.get("zmin", gdf[var].min())
        zmax = locked_colour.get("zmax", gdf[var].max())
    elif "dynamic" in dynamic:
        try:
            buffer_deg = max(2, 15 / (zoom + 0.5))
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
        margin={"r": 0, "t": 30, "l": 0, "b": 0},
        font=dict(family="Segoe UI, Roboto, sans-serif", size=14, color="#333")
    )
    return fig

if __name__ == "__main__":
    app.run(debug=True)
