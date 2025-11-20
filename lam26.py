import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Google Sheets Setup ---
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
skey = st.secrets["gcp_service_account"]
credentials = Credentials.from_service_account_info(skey, scopes=scopes)
client = gspread.authorize(credentials)
url = st.secrets["private_gsheets_url"]

# --- Load Plan Tab ---
try:
    worksheet_plan = client.open_by_url(url).worksheet("Plan")
    plan = worksheet_plan.get_all_records()
except Exception as e:
    st.error(f"❌ Failed to load Plan tab: {e}")
    st.stop()

df_plan = pd.DataFrame(plan)
df_plan.columns = df_plan.columns.str.strip()

# --- Load Runs Tab ---
try:
    worksheet_runs = client.open_by_url(url).worksheet("Runs")
    runs = worksheet_runs.get_all_records()
except Exception as e:
    st.error(f"❌ Failed to load Runs tab: {e}")
    st.stop()

activities = pd.DataFrame(runs)
activities.columns = activities.columns.str.strip()

if activities.empty:
    st.error("❌ No activities found in Runs tab.")
    st.stop()

# --- Enrich Activity Data ---
activities['start_date_local'] = pd.to_datetime(activities['start_date_local'], errors='coerce')
activities['distance'] = pd.to_numeric(activities['distance'], errors='coerce')
activities['average_speed'] = pd.to_numeric(activities['average_speed'], errors='coerce')

activities['miles'] = activities['distance'] / 1609.34
activities['avg_mile_time_sec'] = 1609.34 / activities['average_speed']

def format_pace(x):
    try:
        if pd.isna(x) or not isinstance(x, (int, float)) or x <= 0:
            return None
        minutes = int(x // 60)
        seconds = int(x % 60)
        return f"{minutes}:{seconds:02d}"
    except Exception:
        return None

activities['avg_mile_time'] = activities['avg_mile_time_sec'].apply(format_pace)


# --- Merge with Training Plan ---
df_plan['ID'] = df_plan['ID'].astype(str)
activities['description'] = activities['description'].astype(str)
merged = pd.merge(df_plan, activities, left_on='ID', right_on='description', how='left')

st.subheader("🔗 Merged Plan with Activities")
st.dataframe(merged, use_container_width=True)

# --- Weekly Mileage Chart ---
merged['Weeks_to_Go'] = pd.to_numeric(merged['Weeks_to_Go'], errors='coerce')
valid_rows = merged[
    merged['Weeks_to_Go'].notnull() &
    merged['miles'].apply(lambda x: isinstance(x, (int, float)) and pd.notnull(x))
]

weekly_mileage = (
    valid_rows.groupby('Weeks_to_Go')['miles']
    .sum()
    .reset_index()
    .sort_values('Weeks_to_Go', ascending=False)
)

import plotly.express as px

# Sort descending so higher weeks are on the left
df = weekly_mileage.sort_values('Weeks_to_Go', ascending=False)

fig = px.bar(
    df,
    x='Weeks_to_Go',
    y='miles',
    title='Weekly Mileage',
    labels={'Weeks_to_Go': 'Weeks to Go', 'miles': 'Miles'},
)

# Reverse x-axis
fig.update_layout(xaxis=dict(autorange='reversed'))

st.plotly_chart(fig, use_container_width=True)


# --- Filter for IDs ending in '.6' ---
filtered = merged[merged['ID'].astype(str).str.endswith('.6')]

# --- Ensure datetime format ---
filtered['start_date_local'] = pd.to_datetime(filtered['start_date_local'], errors='coerce')

# --- Sort by date ---
filtered = filtered.sort_values('start_date_local')

# --- Plotly bar chart ---
fig = px.bar(
    filtered,
    x='start_date_local',
    y='miles',
    title='Miles Run by Date (Long Runs)',
    labels={'start_date_local': 'Date', 'miles': 'Miles'},
    hover_data=['name', 'ID']
)

fig.update_layout(xaxis_title='Date', yaxis_title='Miles', bargap=0.2)

st.plotly_chart(fig, use_container_width=True)

# --- Filter for IDs ending in '.3' ---
filtered = merged[merged['ID'].astype(str).str.endswith('.3')]

# --- Ensure datetime format ---
filtered['start_date_local'] = pd.to_datetime(filtered['start_date_local'], errors='coerce')

# --- Sort by date ---
filtered = filtered.sort_values('start_date_local')

import numpy as np

# Filter valid pace values
filtered = filtered[filtered['avg_mile_time_sec'].notnull()]
filtered = filtered[filtered['start_date_local'].notnull()]

# Create tick labels
tickvals = np.arange(300, 480, 30)  # every 30 seconds from 5:00 to 8:00
ticktext = [f"{int(t//60)}:{int(t%60):02d}" for t in tickvals]

fig = px.bar(
    filtered,
    x='start_date_local',
    y='avg_mile_time_sec',
    title='Mile Pace by Date (Track Workouts)',
    labels={'start_date_local': 'Date', 'avg_mile_time_sec': 'Mile Pace'},
    hover_data=['name', 'ID', 'avg_mile_time']
)


fig.update_layout(
    xaxis_title='Date',
    yaxis_title='Mile Pace',
    bargap=0.2,
    yaxis=dict(
        range=[300, 480],  # sets y-axis from 5:00 to 8:00 (in seconds)
        tickmode='array',
        tickvals=tickvals,
        ticktext=ticktext
    )
)

st.plotly_chart(fig, use_container_width=True)



import streamlit as st
import polyline
import folium
import json
from shapely.geometry import LineString
import streamlit.components.v1 as components

# --- Ensure datetime format ---
activities['start_date_local'] = pd.to_datetime(activities['start_date_local'], errors='coerce')

# --- Dropdown sorted by date ---
activities_sorted = activities.sort_values('start_date_local', ascending=False)
run_options = activities_sorted['start_date_local'].dt.strftime('%Y-%m-%d %H:%M') + " | " + activities_sorted['name'].astype(str)
selected = st.selectbox("📅 Select a run to plot:", run_options)

row_index = run_options[run_options == selected].index[0]
polyline_str = activities.loc[row_index, 'polyline']

# --- Decode polyline ---
try:
    coords = polyline.decode(polyline_str)  # returns [(lat, lon), ...]
    if not coords or len(coords) < 2:
        st.warning("⚠️ Not enough coordinates to plot a route.")
        st.stop()
except Exception as e:
    st.error(f"❌ Polyline decode error: {e}")
    st.stop()

# --- Create Folium map centered on start ---
start = coords[0]
end = coords[-1]
m = folium.Map(location=start, zoom_start=14)

# --- Add route polyline ---
folium.PolyLine(locations=coords, color='blue', weight=5).add_to(m)

# --- Add start and end markers ---
folium.Marker(start, popup="Start", icon=folium.Icon(color='green')).add_to(m)
folium.Marker(end, popup="End", icon=folium.Icon(color='red')).add_to(m)

# --- Display map in Streamlit ---
st.subheader("📍 Route Map")
components.html(m._repr_html_(), height=500)
