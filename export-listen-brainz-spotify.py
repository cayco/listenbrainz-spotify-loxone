import configparser
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import urllib.parse
import json
import os
from datetime import datetime, timedelta

config = configparser.ConfigParser()
config.read("config.ini")

LOXONE_IP     = config["loxone"]["ip"]
LOXONE_USER   = config["loxone"]["user"]
LOXONE_PASS   = config["loxone"]["pass"]
LOXONE_VTI_JAMS        = config["loxone"]["vti_weekly_jams"]
LOXONE_VTI_EXPLORATION = config["loxone"]["vti_weekly_exploration"]

LB_USERNAME   = config["listenbrainz"]["username"]
LB_TOKEN      = config["listenbrainz"]["token"]

SPOTIFY_CLIENT_ID     = config["spotify"]["client_id"]
SPOTIFY_CLIENT_SECRET = config["spotify"]["client_secret"]
SPOTIFY_REDIRECT_URI  = config["spotify"]["redirect_uri"]
SPOTIFY_CACHE_PATH    = config["spotify"]["cache_path"]

CACHE_FILE = config["state"]["cache_file"]

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def send_to_loxone(vti, playlist_id):
    print(f"Sending Playlist ID ({playlist_id}) to Loxone VTI '{vti}'...")
    safe_playlist_id = urllib.parse.quote(playlist_id)
    loxone_url = f"http://{LOXONE_IP}/dev/sps/io/{vti}/{safe_playlist_id}"
    try:
        lox_response = requests.get(loxone_url, auth=(LOXONE_USER, LOXONE_PASS), timeout=5)
        if lox_response.status_code == 200:
            print(f"Successfully updated Loxone VTI '{vti}'!")
        else:
            print(f"Failed to update Loxone. Miniserver returned status code: {lox_response.status_code}")
    except Exception as e:
        print(f"Error communicating with Loxone Miniserver: {e}")

def generate_via_troi(jam_type, monday_str):
    """Fallback generator using ListenBrainz Troi PeriodicJamsPatch when /createdfor is stale."""
    from troi.patches.periodic_jams import PeriodicJamsPatch
    print(f"Generating {jam_type} for {LB_USERNAME} (week of {monday_str}) via Troi...")
    patch = PeriodicJamsPatch({
        "user_name": LB_USERNAME,
        "type": jam_type,
        "jam_date": monday_str,
        "token": LB_TOKEN,
        "upload": True,
        "save": False,
        "quiet": True,
        "echo": False,
        "min_recordings": 10,
        "name": None,
        "desc": None,
        "created_for": None,
        "spotify": None,
        "apple_music": None,
        "soundcloud": None,
    })
    playlist_el = patch.generate_playlist()
    jspf = playlist_el.get_jspf()
    mbid = playlist_el.playlists[0].mbid or f"{jam_type}-{monday_str}"
    return mbid, jspf["playlist"]["title"], jspf["playlist"].get("track", [])

cache = load_cache()

# --- 1. AUTHENTICATE WITH SPOTIFY ---
print("Authenticating with Spotify...")
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="playlist-modify-private",
    open_browser=False,
    cache_path=SPOTIFY_CACHE_PATH
))

# --- 2. FETCH LATEST LISTENBRAINZ RECOMMENDATIONS ---
print(f"Fetching 'Created For' playlists for ListenBrainz user: {LB_USERNAME}...")

lb_meta_url = f"https://api.listenbrainz.org/1/user/{LB_USERNAME}/playlists/createdfor"
headers = {"Authorization": f"Token {LB_TOKEN}"}

response = requests.get(lb_meta_url, headers=headers)
playlists_meta = response.json().get("playlists", []) if response.status_code == 200 else []

jams_playlist = None
exploration_playlist = None
for p in playlists_meta:
    title = p.get("playlist", {}).get("title", "")
    if "Weekly Jams" in title and jams_playlist is None:
        jams_playlist = p
    elif "Weekly Exploration" in title and exploration_playlist is None:
        exploration_playlist = p
    if jams_playlist and exploration_playlist:
        break

today = datetime.utcnow().date()
monday = today - timedelta(days=today.weekday())
monday_str = monday.strftime("%Y-%m-%d %a")

to_process = [
    ("weekly-jams", jams_playlist, LOXONE_VTI_JAMS),
    ("weekly-exploration", exploration_playlist, LOXONE_VTI_EXPLORATION),
]

user_id = sp.current_user()["id"]

for jam_type, playlist_meta, vti in to_process:
    week_cache_key = f"{jam_type}:{monday_str}"
    tracks = None
    playlist_mbid = None
    playlist_title = None

    if playlist_meta:
        candidate_mbid = playlist_meta["playlist"]["identifier"].split("/")[-1]
        candidate_title = playlist_meta["playlist"]["title"]
        if candidate_mbid not in cache:
            playlist_mbid = candidate_mbid
            playlist_title = candidate_title

    # If the /createdfor playlist was already exported, check if we need a fresh one for the current week
    if playlist_mbid is None:
        if week_cache_key in cache:
            print(f"Skipping '{jam_type}' for {monday_str} - already exported as Spotify playlist {cache[week_cache_key]}")
            send_to_loxone(vti, cache[week_cache_key])
            continue
        try:
            playlist_mbid, playlist_title, tracks = generate_via_troi(jam_type, monday_str)
        except Exception as e:
            print(f"Failed to generate {jam_type} via Troi: {e}")
            continue

    print(f"\n--- Processing: {playlist_title} ---")

    # --- 3. FETCH TRACK DATA ---
    if tracks is None:
        playlist_url = f"https://api.listenbrainz.org/1/playlist/{playlist_mbid}"
        playlist_data = requests.get(playlist_url, headers=headers).json()
        tracks = playlist_data.get("playlist", {}).get("track", [])

    print(f"Extracting {len(tracks)} tracks and searching Spotify...")

    # --- 4. SEARCH SPOTIFY FOR TRACKS ---
    track_uris = []
    for track in tracks:
        title = track.get("title")
        creator = track.get("creator")
        query = f"{title} {creator}"
        result = sp.search(q=query, type="track", limit=1)
        if result["tracks"]["items"]:
            track_uris.append(result["tracks"]["items"][0]["uri"])
        else:
            print(f"  [!] Could not find on Spotify: {title} by {creator}")

    # --- 5. CREATE SPOTIFY PLAYLIST AND ADD TRACKS ---
    if not track_uris:
        print(f"No tracks found on Spotify for '{playlist_title}', skipping.")
        continue

    print(f"Creating private playlist '{playlist_title}' on Spotify...")
    new_playlist = sp.user_playlist_create(user_id, name=playlist_title, public=False)
    print("Adding tracks to Spotify...")
    sp.playlist_add_items(new_playlist["id"], track_uris)

    # --- 6. SEND PLAYLIST ID TO LOXONE ---
    playlist_id = new_playlist["id"]
    send_to_loxone(vti, playlist_id)

    cache[playlist_mbid] = new_playlist["id"]
    cache[week_cache_key] = new_playlist["id"]
    save_cache(cache)
    print(f"Cached {playlist_mbid} ({week_cache_key}) -> Spotify {new_playlist['id']}")

print("\nDone.")
