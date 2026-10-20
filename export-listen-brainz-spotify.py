"""Export weekly ListenBrainz recommendation playlists to Spotify and Loxone."""
# pylint: disable=invalid-name

import configparser
from datetime import datetime, timedelta
import json
import os
import urllib.parse

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth

config = configparser.ConfigParser()
config.read("config.ini")

LOXONE_IP = config["loxone"]["ip"]
LOXONE_USER = config["loxone"]["user"]
LOXONE_PASS = config["loxone"]["pass"]
LOXONE_VTI_JAMS = config["loxone"]["vti_weekly_jams"]
LOXONE_VTI_EXPLORATION = config["loxone"]["vti_weekly_exploration"]

LB_USERNAME = config["listenbrainz"]["username"]
LB_TOKEN = config["listenbrainz"]["token"]

SPOTIFY_CLIENT_ID = config["spotify"]["client_id"]
SPOTIFY_CLIENT_SECRET = config["spotify"]["client_secret"]
SPOTIFY_REDIRECT_URI = config["spotify"]["redirect_uri"]
SPOTIFY_CACHE_PATH = config["spotify"]["cache_path"]

CACHE_FILE = config["state"]["cache_file"]


def load_cache():
    """Load the exported playlist cache from disk."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as cache_fp:
            return json.load(cache_fp)
    return {}


def save_cache(cache_data):
    """Persist the exported playlist cache to disk."""
    with open(CACHE_FILE, "w", encoding="utf-8") as cache_fp:
        json.dump(cache_data, cache_fp, indent=2)


def send_to_loxone(vti_name, spotify_playlist_id):
    """Send a Spotify playlist ID to a Loxone Virtual Text Input."""
    print(f"Sending Playlist ID ({spotify_playlist_id}) to Loxone VTI \x27{vti_name}\x27...")
    safe_playlist_id = urllib.parse.quote(spotify_playlist_id)
    loxone_url = f"http://{LOXONE_IP}/dev/sps/io/{vti_name}/{safe_playlist_id}"
    try:
        lox_response = requests.get(
            loxone_url, auth=(LOXONE_USER, LOXONE_PASS), timeout=5
        )
        if lox_response.status_code == 200:
            print(f"Successfully updated Loxone VTI \x27{vti_name}\x27!")
        else:
            print(
                "Failed to update Loxone. Miniserver returned status code: "
                f"{lox_response.status_code}"
            )
    except requests.RequestException as exc:
        print(f"Error communicating with Loxone Miniserver: {exc}")


def generate_via_troi(jam_type_slug, week_monday_str):
    """Fallback generator using ListenBrainz Troi PeriodicJamsPatch."""
    # pylint: disable=import-outside-toplevel,import-error
    from troi.patches.periodic_jams import PeriodicJamsPatch

    print(
        f"Generating {jam_type_slug} for {LB_USERNAME} "
        f"(week of {week_monday_str}) via Troi..."
    )
    patch = PeriodicJamsPatch({
        "user_name": LB_USERNAME,
        "type": jam_type_slug,
        "jam_date": week_monday_str,
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
    mbid = playlist_el.playlists[0].mbid or f"{jam_type_slug}-{week_monday_str}"
    return mbid, jspf["playlist"]["title"], jspf["playlist"].get("track", [])


def fetch_createdfor_playlists(headers):
    """Fetch Weekly Jams and Weekly Exploration metadata from ListenBrainz."""
    lb_meta_url = (
        f"https://api.listenbrainz.org/1/user/{LB_USERNAME}/playlists/createdfor"
    )
    response = requests.get(lb_meta_url, headers=headers, timeout=15)
    playlists_meta = (
        response.json().get("playlists", []) if response.status_code == 200 else []
    )

    jams_playlist = None
    exploration_playlist = None
    for item in playlists_meta:
        title = item.get("playlist", {}).get("title", "")
        if "Weekly Jams" in title and jams_playlist is None:
            jams_playlist = item
        elif "Weekly Exploration" in title and exploration_playlist is None:
            exploration_playlist = item
        if jams_playlist and exploration_playlist:
            break
    return jams_playlist, exploration_playlist


def resolve_playlist_tracks(jam_type, playlist_meta, monday_str, cache, headers):
    """Resolve playlist MBID, title, and track list from ListenBrainz or Troi."""
    week_cache_key = f"{jam_type}:{monday_str}"
    playlist_mbid = None
    playlist_title = None
    tracks = None

    if playlist_meta:
        candidate_mbid = playlist_meta["playlist"]["identifier"].split("/")[-1]
        candidate_title = playlist_meta["playlist"]["title"]
        if candidate_mbid not in cache:
            playlist_mbid = candidate_mbid
            playlist_title = candidate_title

    if playlist_mbid is None:
        if week_cache_key in cache:
            return None, None, None
        try:
            playlist_mbid, playlist_title, tracks = generate_via_troi(
                jam_type, monday_str
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"Failed to generate {jam_type} via Troi: {exc}")
            return None, None, None

    if tracks is None:
        playlist_url = f"https://api.listenbrainz.org/1/playlist/{playlist_mbid}"
        playlist_data = requests.get(
            playlist_url, headers=headers, timeout=15
        ).json()
        tracks = playlist_data.get("playlist", {}).get("track", [])

    return playlist_mbid, playlist_title, tracks


def search_spotify_tracks(sp_client, tracks):
    """Search Spotify for a list of JSPF tracks and return matching URIs."""
    track_uris = []
    for track in tracks:
        title = track.get("title")
        creator = track.get("creator")
        result = sp_client.search(q=f"{title} {creator}", type="track", limit=1)
        if result["tracks"]["items"]:
            track_uris.append(result["tracks"]["items"][0]["uri"])
        else:
            print(f"  [!] Could not find on Spotify: {title} by {creator}")
    return track_uris


def process_playlist_item(item_spec, context):
    """Export a single ListenBrainz playlist to Spotify and update Loxone."""
    jam_type, playlist_meta, vti = item_spec
    monday_str = context["monday_str"]
    cache = context["cache"]
    week_cache_key = f"{jam_type}:{monday_str}"

    playlist_mbid, playlist_title, tracks = resolve_playlist_tracks(
        jam_type, playlist_meta, monday_str, cache, context["headers"]
    )
    if playlist_mbid is None:
        if week_cache_key in cache:
            cached_id = cache[week_cache_key]
            print(
                f"Skipping \x27{jam_type}\x27 for {monday_str} - "
                f"already exported as Spotify playlist {cached_id}"
            )
            send_to_loxone(vti, cached_id)
        return

    print(f"\n--- Processing: {playlist_title} ---")
    print(f"Extracting {len(tracks)} tracks and searching Spotify...")
    sp_client = context["sp_client"]
    track_uris = search_spotify_tracks(sp_client, tracks)

    if not track_uris:
        print(f"No tracks found on Spotify for \x27{playlist_title}\x27, skipping.")
        return

    print(f"Creating private playlist \x27{playlist_title}\x27 on Spotify...")
    new_playlist = sp_client.user_playlist_create(
        context["user_id"], name=playlist_title, public=False
    )
    print("Adding tracks to Spotify...")
    sp_client.playlist_add_items(new_playlist["id"], track_uris)

    playlist_id = new_playlist["id"]
    send_to_loxone(vti, playlist_id)

    cache[playlist_mbid] = playlist_id
    cache[week_cache_key] = playlist_id
    save_cache(cache)
    print(f"Cached {playlist_mbid} ({week_cache_key}) -> Spotify {playlist_id}")


def main():
    """Authenticate, export ListenBrainz playlists to Spotify, and update Loxone."""
    print("Authenticating with Spotify...")
    sp_client = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="playlist-modify-private",
        open_browser=False,
        cache_path=SPOTIFY_CACHE_PATH,
    ))

    print(f"Fetching \x27Created For\x27 playlists for ListenBrainz user: {LB_USERNAME}...")
    headers = {"Authorization": f"Token {LB_TOKEN}"}
    jams_playlist, exploration_playlist = fetch_createdfor_playlists(headers)

    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    context = {
        "sp_client": sp_client,
        "user_id": sp_client.current_user()["id"],
        "headers": headers,
        "monday_str": monday.strftime("%Y-%m-%d %a"),
        "cache": load_cache(),
    }

    for item_spec in (
        ("weekly-jams", jams_playlist, LOXONE_VTI_JAMS),
        ("weekly-exploration", exploration_playlist, LOXONE_VTI_EXPLORATION),
    ):
        process_playlist_item(item_spec, context)

    print("\nDone.")


if __name__ == "__main__":
    main()
