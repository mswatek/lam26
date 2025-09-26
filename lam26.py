import streamlit as st
import pandas as pd
import time
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Google Sheets Setup ---
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
skey = st.secrets["gcp_service_account"]
credentials = Credentials.from_service_account_info(skey, scopes=scopes)
client = gspread.authorize(credentials)
url = st.secrets["private_gsheets_url"]
sheet_name = "Plan"
worksheet = client.open_by_url(url).worksheet(sheet_name)

plan = worksheet.get_all_records()
df_plan = pd.DataFrame(plan)
df_plan.columns = df_plan.columns.str.strip()  # normalize column names
st.subheader("📋 Training Plan")
st.dataframe(df_plan, use_container_width=True)

# --- Strava API Setup ---
auth_url = "https://www.strava.com/oauth/token"
activites_url = "https://www.strava.com/api/v3/athlete/activities"

payload = {
    'client_id': "164663",
    'client_secret': '0e789defd5387984d406bdd0f07b93d9af5670d7',
    'refresh_token': '8d9eadbe6cff750eb0b103f3157f165b4cc0704f',
    'grant_type': "refresh_token",
    'f': 'json'
}

res = requests.post(auth_url, data=payload, verify=False)
access_token = res.json()['access_token']

# Convert September 1, 2025 to Unix timestamp
after_timestamp = int(time.mktime(datetime(2025, 9, 1).timetuple()))

header = {'Authorization': 'Bearer ' + access_token}
param = {
    'per_page': 200,
    'page': 1,
    'after': after_timestamp
}
my_dataset = requests.get(activites_url, headers=header, params=param).json()

if isinstance(my_dataset, dict) and 'message' in my_dataset:
    st.error(f"❌ Strava API error: {my_dataset['message']}")
    st.stop()

# --- Detailed Activity Pull ---
detailed_activities = []

for activity in my_dataset:
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
            'start_date_local': detail.get('start_date_local')
        })
    else:
        st.warning(f"⚠️ Skipping malformed activity: {activity}")

activities = pd.DataFrame(detailed_activities)

# --- Enrich Activity Data ---
activities['start_date_local'] = pd.to_datetime(activities['start_date_local'])
activities['miles'] = activities['distance'] / 1609.34
activities['avg_mile_time_sec'] = 1609.34 / activities['average_speed']
activities['avg_mile_time'] = activities['avg_mile_time_sec'].apply(
    lambda x: f"{int(x // 60)}:{int(x % 60):02d}" if pd.notnull(x) and x > 0 else None
)

st.subheader("🏃 Strava Activities")
st.dataframe(activities, use_container_width=True)

# --- Merge with Training Plan ---
df_plan['ID'] = df_plan['ID'].astype(str)
merged = pd.merge(df_plan, activities, left_on='ID', right_on='description', how='left')

st.subheader("🔗 Merged Plan with Activities")
st.dataframe(merged, use_container_width=True)

# --- Weekly Mileage Chart Using 'Weeks_to_Go' ---
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

if not weekly_mileage.empty:
    st.subheader("📊 Mileage by Weeks to Go (Descending)")
    st.bar_chart(weekly_mileage.set_index('Weeks_to_Go')['miles'])
else:
    st.warning("No valid mileage data found for charting.")

