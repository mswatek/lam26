import streamlit as st
import pandas as pd
from datetime import datetime
import time
import gspread
import requests
import json
from google.oauth2.service_account import Credentials
import urllib3
import polyline
import folium
import plotly.express as px
from shapely.geometry import LineString, mapping
import streamlit.components.v1 as components


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

# --- Heart Rate ---
activities['average_heartrate'] = pd.to_numeric(activities['average_heartrate'], errors='coerce')

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


# Ensure datetime format
merged['start_date_local'] = pd.to_datetime(merged['start_date_local'], errors='coerce')

# Sort by date descending
merged_sorted = merged.sort_values('start_date_local', ascending=False)

# Build dropdown options: show ID instead of name
run_options = merged_sorted['start_date_local'].dt.strftime('%Y-%m-%d %H:%M') + " | ID " + merged_sorted['ID'].astype(str)

# Display dropdown
selected = st.selectbox("📅 Select a run to plot:", run_options)

# Get the selected row index
row_index = run_options[run_options == selected].index[0]

# Extract polyline
polyline_str = merged_sorted.loc[row_index, 'polyline']

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


############# UPDATE GOOGLE SHEET USING STRAVA API #############

st.divider()

def update_strava_sheet():
    st.info("🔄 Fetching Strava activities and updating Google Sheet...")

    # --- Disable SSL Warnings ---
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # --- Google Sheets Setup ---
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    skey = st.secrets["gcp_service_account"]
    credentials = Credentials.from_service_account_info(skey, scopes=scopes)
    client = gspread.authorize(credentials)
    url = st.secrets["private_gsheets_url"]
    worksheet = client.open_by_url(url).worksheet("Runs")

    # --- Strava API Setup ---
    auth_url = "https://www.strava.com/oauth/token"
    activities_url = "https://www.strava.com/api/v3/athlete/activities"

    payload = {
        'client_id': st.secrets["strava_client_id"],
        'client_secret': st.secrets["strava_client_secret"],
        'refresh_token': st.secrets["strava_refresh_token"],
        'grant_type': "refresh_token"
    }

    res = requests.post(auth_url, data=payload, verify=False)
    if res.status_code != 200:
        st.error("❌ Failed to authenticate with Strava.")
        st.text(f"Status code: {res.status_code}")
        st.text(f"Response: {res.text}")
        return

    access_token = res.json().get('access_token')
    if not access_token:
        st.error("❌ No access token received from Strava.")
        return

    # --- Fetch Activities ---
    after_timestamp = int(time.mktime(datetime(2025, 9, 1).timetuple()))
    header = {'Authorization': f'Bearer {access_token}'}
    param = {'per_page': 50, 'page': 1, 'after': after_timestamp}

    raw_response = requests.get(activities_url, headers=header, params=param).json()
    detailed_activities = []

    for activity in raw_response:
        if isinstance(activity, dict) and 'id' in activity:
            activity_id = activity['id']
            detail_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
            detail = requests.get(detail_url, headers=header).json()

            detailed_activities.append({
                'name': detail.get('name'),
                'description': detail.get('description', ''),
                'private_note': detail.get('private_note', ''),
                'type': detail.get('type'),
                'distance': detail.get('distance'),
                'moving_time': detail.get('moving_time'),
                'average_speed': detail.get('average_speed'),
                'max_speed': detail.get('max_speed'),
                'total_elevation_gain': detail.get('total_elevation_gain'),
                'start_date_local': detail.get('start_date_local'),
                'map': detail.get('map') if 'map' in detail else None,
                'average_heartrate': detail.get('average_heartrate')
            })

            time.sleep(0.5)  # throttle to avoid rate limit

    # --- Convert to DataFrame ---
    activities = pd.DataFrame(detailed_activities)

    def polyline_to_geojson(summary_polyline):
        try:
            coords = polyline.decode(summary_polyline)
            line = LineString(coords)
            return json.dumps(mapping(line))  # GeoJSON LineString
        except Exception:
            return None

    activities['polyline'] = activities['map'].apply(
        lambda m: m.get('polyline') if isinstance(m, dict) and m.get('polyline') else None
    )
    activities['geojson'] = activities['polyline'].apply(polyline_to_geojson)

    activities['start_date_local'] = pd.to_datetime(activities['start_date_local'])
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

    # --- Upload to Google Sheet ---
    worksheet.clear()
    activities = activities.astype(str)
    worksheet.update([activities.columns.values.tolist()] + activities.values.tolist())

    st.success("✅ Strava activities uploaded to Google Sheet.")

if st.button("🔄 Update Strava Activities"):
    try:
        update_strava_sheet()
    except Exception as e:
        st.error(f"❌ Update failed: {e}")
