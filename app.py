import streamlit as st
import osmnx as ox
import networkx as nx
import numpy as np
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Urban Air Stress Navigator – Hamburg", layout="wide")

st.title("Urban Air Stress Navigator – Hamburg")
st.caption("MVP-Prototyp: Personalisierung + Start/Ziel per Klick + Routenvergleich")

# ---------- Profile (1) ----------
profile = st.selectbox(
    "Choose user profile:",
    ["Birkenpollen-Allergiker", "Kind", "Sport"]
)

if profile == "Birkenpollen-Allergiker":
    W_P, W_PM, W_NO2, W_T = 0.40, 0.25, 0.20, 0.15
elif profile == "Kind":
    W_P, W_PM, W_NO2, W_T = 0.20, 0.35, 0.30, 0.15
else:  # Sport
    W_P, W_PM, W_NO2, W_T = 0.20, 0.40, 0.25, 0.15

# Wie stark soll der Algorithmus "Stress vermeiden"?
ALPHA = st.slider("Avoid air stress intensity (ALPHA)", 0, 1200, 400, 50)

# ---------- Area ----------
PLACE = st.text_input("Area (OSM place query):", "HafenCity, Hamburg, Germany")

if st.button("Load Area"):
    st.session_state.start = None
    st.session_state.end = None
    st.cache_data.clear()
    st.rerun()

# ---------- Helpers ----------
def minmax(x, xmin, xmax):
    if xmax == xmin:
        return 0.0
    return float(np.clip((x - xmin) / (xmax - xmin), 0, 1))

ROAD_STRESS = {
    "motorway": 1.0, "trunk": 0.9, "primary": 0.8,
    "secondary": 0.65, "tertiary": 0.45,
    "residential": 0.20, "living_street": 0.10,
    "service": 0.20, "unclassified": 0.30, "pedestrian": 0.10, "path": 0.10, "footway": 0.10
}

def traffic_proxy(highway_value):
    if isinstance(highway_value, list) and len(highway_value) > 0:
        highway_value = highway_value[0]
    return ROAD_STRESS.get(str(highway_value), 0.30)

def uasi_color(x):
    if x < 0.45:
        return "green"
    elif x < 0.7:
        return "orange"
    return "red"

# ---------- Session state for (3) click points ----------
if "start" not in st.session_state:
    st.session_state.start = None
if "end" not in st.session_state:
    st.session_state.end = None

colA, colB, colC = st.columns([1,1,1])
with colA:
    if st.button("Reset Start"):
        st.session_state.start = None
with colB:
    if st.button("Reset End"):
        st.session_state.end = None
with colC:
    if st.button("Reset Both"):
        st.session_state.start = None
        st.session_state.end = None

# ---------- Load graph ----------
@st.cache_data(show_spinner=True)
def load_graph(place: str):
    G = ox.graph_from_place(place, network_type="walk")
    return G

with st.spinner("Loading street network from OpenStreetMap..."):
    G = load_graph(PLACE)

# ---------- Dummy environment data (replace later with real sources) ----------
# IMPORTANT: Für MVP reichen Dummy-Werte; später ersetzen wir sie durch echte Daten.

st.subheader("Environmental Conditions (Demo Controls)")

POLLEN_BIRCH = st.slider("Birch pollen intensity (0–1)", 0.0, 1.0, 0.7, 0.05)
PM25 = st.slider("PM2.5 (µg/m³)", 0.0, 80.0, 18.0, 1.0)
NO2 = st.slider("NO₂ (µg/m³)", 0.0, 150.0, 35.0, 1.0)

# Normalization bounds (simple MVP thresholds)
PM25_MIN, PM25_MAX = 0.0, 50.0
NO2_MIN, NO2_MAX = 0.0, 100.0
P_MIN, P_MAX = 0.0, 1.0
T_MIN, T_MAX = 0.0, 1.0

for u, v, k, data in G.edges(keys=True, data=True):
    t = traffic_proxy(data.get("highway", "unclassified"))

    # ВСЕ 4 norm должны быть определены:
    p_norm = minmax(POLLEN_BIRCH, P_MIN, P_MAX)

    t_norm = minmax(t, T_MIN, T_MAX)

    pm_local = PM25 * (1 + 0.8 * t_norm)
    no2_local = NO2 * (1 + 1.2 * t_norm)

    pm_norm = minmax(pm_local, PM25_MIN, PM25_MAX)
    no2_norm = minmax(no2_local, NO2_MIN, NO2_MAX)

    uasi = (W_P * p_norm) + (W_PM * pm_norm) + (W_NO2 * no2_norm) + (W_T * t_norm)
    data["uasi"] = uasi

length = data.get("length", 1.0)  # osmnx usually provides it
data["uasi_cost"] = length + ALPHA * uasi

nodes, edges = ox.graph_to_gdfs(G)

center = [nodes.geometry.y.mean(), nodes.geometry.x.mean()]
m = folium.Map(location=center, zoom_start=14)

# draw edges
for _, row in edges.iterrows():
    geom = row.geometry
    if geom is None:
        continue
    uasi = float(row.get("uasi", 0.0))
    coords = [(lat, lon) for lon, lat in geom.coords]
    folium.PolyLine(coords, weight=3, color=uasi_color(uasi), opacity=0.8).add_to(m)

# show current markers
if st.session_state.start:
    folium.Marker(st.session_state.start, tooltip="Start", icon=folium.Icon(color="blue")).add_to(m)
if st.session_state.end:
    folium.Marker(st.session_state.end, tooltip="End", icon=folium.Icon(color="red")).add_to(m)

st.subheader("Click on the map to set Start and End")
st.write("First click = Start, second click = End. Use Reset buttons if needed.")

map_state = st_folium(m, width=1100, height=650, key=f"map-{PLACE}")

# capture click
clicked = map_state.get("last_clicked")
if clicked:
    lat, lon = clicked["lat"], clicked["lng"]
    if st.session_state.start is None:
        st.session_state.start = (lat, lon)
        st.rerun()
    elif st.session_state.end is None:
        st.session_state.end = (lat, lon)
        st.rerun()

# ---------- Routing once we have start+end ----------
def route_length(route_nodes):
    total = 0.0
    for uu, vv in zip(route_nodes[:-1], route_nodes[1:]):
        edge_dict = G.get_edge_data(uu, vv)
        if not edge_dict:
            continue
        edge_data = min(edge_dict.values(), key=lambda d: d.get("length", 1e9))
        total += float(edge_data.get("length", 0.0))
    return total

def route_uasi_sum(route_nodes):
    total = 0.0
    for uu, vv in zip(route_nodes[:-1], route_nodes[1:]):
        edge_dict = G.get_edge_data(uu, vv)
        if not edge_dict:
            continue
        edge_data = min(edge_dict.values(), key=lambda d: d.get("length", 1e9))
        total += float(edge_data.get("uasi", 0.0))
    return total

def route_to_coords(route_nodes):
    coords = []
    for node in route_nodes:
        coords.append((G.nodes[node]["y"], G.nodes[node]["x"]))
    return coords

if st.session_state.start and st.session_state.end:
    orig_node = ox.distance.nearest_nodes(G, X=st.session_state.start[1], Y=st.session_state.start[0])
    dest_node = ox.distance.nearest_nodes(G, X=st.session_state.end[1], Y=st.session_state.end[0])

    shortest_route = nx.shortest_path(G, orig_node, dest_node, weight="length")
    least_stress_route = nx.shortest_path(G, orig_node, dest_node, weight="uasi_cost")

    # new map with routes
    m2 = folium.Map(location=center, zoom_start=14)

    for _, row in edges.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        uasi = float(row.get("uasi", 0.0))
        coords = [(lat, lon) for lon, lat in geom.coords]
        folium.PolyLine(coords, weight=3, color=uasi_color(uasi), opacity=0.6).add_to(m2)

    # markers
    folium.Marker(st.session_state.start, tooltip="Start", icon=folium.Icon(color="blue")).add_to(m2)
    folium.Marker(st.session_state.end, tooltip="End", icon=folium.Icon(color="red")).add_to(m2)

    # routes
    folium.PolyLine(route_to_coords(shortest_route), weight=7, color="blue", opacity=0.95,
                    tooltip="Shortest route").add_to(m2)
    folium.PolyLine(route_to_coords(least_stress_route), weight=7, color="purple", opacity=0.95,
                    tooltip="Least-stress route").add_to(m2)

    st.subheader("Routes comparison")
    st_folium(m2, width=1100, height=650)

    # metrics
    s_len = route_length(shortest_route)
    ls_len = route_length(least_stress_route)
    s_uasi = route_uasi_sum(shortest_route)
    ls_uasi = route_uasi_sum(least_stress_route)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Shortest length (m)", f"{s_len:.1f}")
    c2.metric("Least-stress length (m)", f"{ls_len:.1f}")
    c3.metric("Shortest UASI sum", f"{s_uasi:.3f}")
    c4.metric("Least-stress UASI sum", f"{ls_uasi:.3f}")

    st.info("Hinweis: Aktuell nutzt das MVP Dummy-Werte für Pollen/PM2.5/NO₂. "
            "Als nächster Schritt ersetzen wir diese durch reale Datenquellen für Hamburg.")
else:
    st.warning("Bitte klicke zuerst Start und dann End auf der Karte.")