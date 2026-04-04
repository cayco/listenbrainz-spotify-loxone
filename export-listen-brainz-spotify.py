import configparser
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import urllib.parse
import json
import os

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
if response.status_code != 200:
    print(f"Failed to fetch ListenBrainz playlists: {response.text}")
    exit()

playlists_meta = response.json().get("playlists", [])

# Find both playlist types
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

if not jams_playlist and not exploration_playlist:
    print("Could not find any Weekly Jams or Explorations on your ListenBrainz profile.")
    exit()

to_process = []
if jams_playlist:
    to_process.append((jams_playlist, LOXONE_VTI_JAMS))
else:
    print("Warning: Weekly Jams playlist not found, skipping.")

if exploration_playlist:
    to_process.append((exploration_playlist, LOXONE_VTI_EXPLORATION))
else:
    print("Warning: Weekly Exploration playlist not found, skipping.")

user_id = sp.current_user()["id"]

for playlist_meta, vti in to_process:
    playlist_mbid = playlist_meta["playlist"]["identifier"].split("/")[-1]
    playlist_title = playlist_meta["playlist"]["title"]
    
    if playlist_mbid in cache:
        print(f"Skipping '{playlist_title}' — already exported as Spotify playlist {cache[playlist_mbid]}")
        continue
    
    print(f"\n--- Processing: {playlist_title} ---")

    # --- 3. FETCH TRACK DATA ---
    playlist_url = f"https://api.listenbrainz.org/1/playlist/{playlist_mbid}"
    playlist_data = requests.get(playlist_url, headers=headers).json()
    tracks = playlist_data.get("playlist", {}).get("track", [])
    print(f"Extracting {len(tracks)} tracks and searching Spotify...")

    # --- 4. SEARCH SPOTIFY FOR TRACKS ---
    track_uris = []
    for track in tracks:
        title = track.get("title")
        creator = track.get("creator")
        query = f"track:{title} artist:{creator}"
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
    
    cache[playlist_mbid] = new_playlist["id"]
    save_cache(cache)
    print(f"Cached MBID {playlist_mbid} -> Spotify {new_playlist['id']}")

print("\nDone.")

