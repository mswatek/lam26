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
import numpy as np
from shapely.geometry import LineString, mapping
import streamlit.components.v1 as components


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- App Title ---
st.title("🏅 LA Marathon 2026 Tracker")

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
    text='miles'
)

fig.update_traces(texttemplate='%{text:.1f}', textposition='outside')  # format labels
fig.update_layout(xaxis=dict(autorange='reversed'))
st.plotly_chart(fig, width="stretch")



# --- Tabs for Long Runs and Track Workouts ---
tab1, tab2 = st.tabs(["🏃 Long Runs", "🏃‍♂️ Track Workouts"])

with tab1:
    # Long Runs chart
    filtered_long = merged[merged['ID'].astype(str).str.endswith('.6')].copy()
    filtered_long['start_date_local'] = pd.to_datetime(filtered_long['start_date_local'], errors='coerce')
    filtered_long = filtered_long.sort_values('start_date_local')

    fig_long = px.bar(
        filtered_long,
        x='start_date_local',
        y='miles',
        title='Miles Run by Date (Long Runs)',
        labels={'start_date_local': 'Date', 'miles': 'Miles'},
        hover_data=['name', 'ID'],
        text='miles'
    )
    fig_long.update_traces(texttemplate='%{text:.1f}', textposition='outside')  # format labels
    fig_long.update_layout(xaxis_title='Date', yaxis_title='Miles', bargap=0.2)
    st.plotly_chart(fig_long, width="stretch")

with tab2:
    # Track Workouts chart
    filtered_track = merged[merged['name'].astype(str).str.contains("Track", case=False, na=False)].copy()
    filtered_track['start_date_local'] = pd.to_datetime(filtered_track['start_date_local'], errors='coerce')
    filtered_track = filtered_track.sort_values('start_date_local')
    filtered_track = filtered_track[filtered_track['avg_mile_time_sec'].notnull()]
    filtered_track = filtered_track[filtered_track['start_date_local'].notnull()]

    tickvals = np.arange(300, 480, 30)
    ticktext = [f"{int(t//60)}:{int(t%60):02d}" for t in tickvals]

    fig_track = px.bar(
        filtered_track,
        x='start_date_local',
        y='avg_mile_time_sec',
        title='Mile Pace by Date (Track Workouts)',
        labels={'start_date_local': 'Date', 'avg_mile_time_sec': 'Mile Pace'},
        hover_data=['name', 'ID', 'avg_mile_time'],
        text='avg_mile_time'
    )
    fig_track.update_traces(textposition='outside')
    fig_track.update_layout(
        xaxis_title='Date',
        yaxis_title='Mile Pace',
        bargap=0.2,
        yaxis=dict(range=[300, 480], tickmode='array', tickvals=tickvals, ticktext=ticktext)
    )
    st.plotly_chart(fig_track, width="stretch")


# --- Table with plan and run data ---
st.subheader("🔗 Full Training Table")

# Select only the desired columns
columns_to_show = [
    "Weeks_to_Go",
    "ID",
    "Date",
    "Day",
    "Activity",
    "Notes",
    "miles",
    "avg_mile_time",
    "average_heartrate",
    "private_note",
]

# Use .loc to avoid SettingWithCopy warnings
merged_display = merged.loc[:, columns_to_show]

st.dataframe(merged_display, width="stretch", hide_index=True)


# --- Route Map section ---
st.subheader("📍 Route Map")


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
    detailed_activities = []
    page = 1

    while True:
        param = {'per_page': 50, 'page': page, 'after': after_timestamp}
        raw_response = requests.get(activities_url, headers=header, params=param).json()

        if not raw_response:  # stop when no more activities
            break

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
                time.sleep(0.5)

        page += 1


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



