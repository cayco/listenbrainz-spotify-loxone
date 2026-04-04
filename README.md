# ListenBrainz → Spotify → Loxone/LMS

Automatically exports your ListenBrainz **Weekly Jams** and **Weekly Exploration** playlists to Spotify, then pushes the resulting playlist IDs to a [Loxone](https://www.loxone.com/) Miniserver via Virtual Text Inputs (VTI) — so your smart home can immediately play them through [Logitech Media Server (LMS)](https://lyrion.org/).

---

## How It Works

```
ListenBrainz (Weekly Jams / Weekly Exploration)
        │
        ▼
Search each track on Spotify
        │
        ▼
Create private Spotify playlist
        │
        ▼
Push playlist ID → Loxone VTI16 (Jams) / VTI17 (Exploration)
        │
        ▼
Loxone triggers LMS via JSON-RPC → plays playlist
```

---

## Prerequisites

- Python 3.8+
- [pyenv](https://github.com/pyenv/pyenv) + [pyenv-virtualenv](https://github.com/pyenv/pyenv-virtualenv)
- A [ListenBrainz](https://listenbrainz.org/) account with Weekly Jams / Exploration enabled
- A [Spotify Developer app](https://developer.spotify.com/dashboard) (Client ID + Secret)
- A Loxone Miniserver reachable on your local network
- Logitech Media Server with a Spotify plugin (e.g. [spotty](https://github.com/michaelherger/Spotty-Plugin))

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/youruser/listenbrainz-spotify-loxone.git
cd listenbrainz-spotify-loxone
```

### 2. Set up a pyenv virtualenv

```bash
# Install the desired Python version if you haven't already
pyenv install 3.11.9

# Create a virtualenv for this project
pyenv virtualenv 3.11.9 listenbrainz-spotify

# Activate it (or let the .python-version file do it automatically)
pyenv local listenbrainz-spotify
```

This creates a `.python-version` file in the project directory — pyenv will activate the right environment automatically whenever you `cd` into it.

### 3. Install dependencies

```bash
pip install requests spotipy
```

---

## Configuration

Copy the example config and fill in your details:

```bash
cp config.ini.example config.ini
```

**`config.ini`:**

```ini
[loxone]
ip = 10.0.2.81
user = admin
pass = your_loxone_password
vti_weekly_jams = VTI16
vti_weekly_exploration = VTI17

[listenbrainz]
username = your_lb_username
token = your_lb_token

[spotify]
client_id = your_spotify_client_id
client_secret = your_spotify_client_secret
redirect_uri = http://127.0.0.1:9090/callback
cache_path = /home/youruser/.spotify_cache

[state]
cache_file = /home/youruser/.listenbrainz_export_cache.json
```

> ⚠️ **Never commit `config.ini` to Git.** It is listed in `.gitignore` by default.

### Getting your ListenBrainz token

Log in to ListenBrainz → go to [your profile settings](https://listenbrainz.org/profile/) → copy your **User Token**.

### Getting your Spotify credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create an app
3. Add `http://127.0.0.1:9090/callback` as a Redirect URI
4. Copy the **Client ID** and **Client Secret**

---

## Usage

```bash
python export-listen-brainz-spotify.py
```

On the first run, Spotify will ask you to authorize the app in your browser. After that, credentials are cached and the script runs fully unattended.

### Duplicate prevention

The script keeps a local JSON cache of exported ListenBrainz playlist MBIDs. If a playlist has already been exported in a previous run, it is skipped — so running the script multiple times or on a schedule will never create duplicate Spotify playlists.

The cache file path is set in `config.ini` under `[state] > cache_file`.

---

## Automating with cron

Because cron runs in a minimal shell environment, it won't pick up pyenv shims automatically. Use the **full path to the virtualenv's Python interpreter** instead:

```bash
# Find the full path
pyenv which python
# e.g. /home/youruser/.pyenv/versions/listenbrainz-spotify/bin/python
```

Then add to your crontab (`crontab -e`):

```
0 9 * * 1 /home/youruser/.pyenv/versions/listenbrainz-spotify/bin/python /path/to/export-listen-brainz-spotify.py >> /var/log/lb_export.log 2>&1
```

This runs every Monday at 09:00, which aligns with when ListenBrainz refreshes its weekly playlists. Adjust the schedule to your preference — duplicate prevention ensures re-runs are always safe.

---

## Playing the Playlist in LMS

Once Loxone receives the Spotify playlist ID via VTI, you can trigger playback on any LMS player by calling the LMS **JSON-RPC endpoint**:

```
POST http://<lms-ip>:9000/jsonrpc.js
```

With the following JSON body:

```json
{
  "id": 1,
  "method": "slim.request",
  "params": [
    "40:ed:cf:58:4c:31",
    ["playlist", "play", "spotify:playlist:<playlist_id>"]
  ]
}
```

- `40:ed:cf:58:4c:31` — the MAC address of your target LMS player
- `spotify:playlist:<playlist_id>` — the Spotify playlist ID stored in the VTI

### Loxone VTI Rotation

Both VTIs are updated on every script run — VTI16 always holds the latest Weekly Jams ID, VTI17 always holds the latest Weekly Exploration ID.

To keep the listening experience varied, the Loxone program alternates which VTI it reads on a daily schedule:

| Day | Playlist played |
|-----|----------------|
| Monday | VTI16 — Weekly Jams |
| Tuesday | VTI17 — Weekly Exploration |
| Wednesday | VTI16 — Weekly Jams |
| Thursday | VTI17 — Weekly Exploration |
| ... | alternates |

This is implemented directly in Loxone using a time-based trigger that checks the current weekday parity and reads from the appropriate VTI, constructing the `spotify:playlist:<id>` URI before passing it to the LMS JSON-RPC call. See the screenshot below for an example of how this looks in Loxone Config.

![Loxone VTI rotation config](./lox.png)

---

## Project Structure

```
.
├── export-listen-brainz-spotify.py   # Main script
├── config.ini                        # Your local config (git-ignored)
├── config.ini.example                # Safe template to commit
├── .python-version                   # pyenv virtualenv pin
└── README.md
```

---

## License

MIT
