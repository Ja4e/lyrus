"""
MIT License
Copyright (c) 2025 Saul Gman
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
"""
CMUS Lyrics Viewer with Synchronized Display
Displays time-synced lyrics for cmus music player using multiple lyric sources
"""

# ==============
#  DEPENDENCIES
# ==============
import curses  # Terminal UI framework
import redis   # Caching
import aiohttp  # Async HTTP client
import threading # For tracking Status
import concurrent.futures # For concurrent API requests
from concurrent.futures import ThreadPoolExecutor
import subprocess  # For cmus interaction
import re  # Regular expressions
import os  # File system operations
import bisect  # For efficient list searching
import time  # Timing functions
import textwrap  # Text formatting
import requests  # HTTP requests for lyric APIs
import urllib.parse  # URL encoding
import syncedlyrics  # Lyric search library
import multiprocessing  # Parallel lyric fetching
import asyncio
from datetime import datetime, timedelta  # Time handling for logs
from mpd import MPDClient  # MPD support
import socket # used for listening for common mpd port 6600
import json

# ==============
#  CONFIGURATION
# ==============
config_files = ["config.json", "config1.json", "config2.json"]

def load_config():
	"""Load and merge configuration from file and environment"""
	default_config = {
		"global": {
			"logs_dir": "logs",
			"lyrics_timeout_log": "lyrics_timeouts.log",
			"debug_log": "debug.log",
			"log_retention_days": 10,
			"max_debug_count": 100,
			"enable_debug": {"env": "DEBUG", "default": "0"}
		},
		"player": {
			"prioritize_cmus": True,
			"mpd": {
				"host": {"env": "MPD_HOST", "default": "localhost"},
				"port": {"env": "MPD_PORT", "default": 6600},
				"password": {"env": "MPD_PASSWORD", "default": None},
				"timeout": 10
			}
		},
		"redis": {
			"enabled": True,
			"host": {"env": "REDIS_HOST", "default": "localhost"},
			"port": {"env": "REDIS_PORT", "default": 6379}
		},
		"status_messages": {
			"start": "Starting lyric search...",
			"local": "Checking local files",
			"synced": "Searching online sources",
			"lrc_lib": "Checking LRCLIB database",
			"instrumental": "Instrumental track detected",
			"time_out": "In time-out log",
			"failed": "No lyrics found",
			"mpd": "scanning for MPD activity",
			"cmus": "loading cmus",
			"done": "Loaded",
			"clear": ""
		},
		"terminal_states": ["done", "instrumental", "time_out", "failed", "mpd", "clear", "cmus"],
		"lyrics": {
			"search_timeout": 15,
			"cache_dir": "synced_lyrics",
			"local_extensions": ["a2", "lrc", "txt"],
			"validation": {"title_match_length": 15, "artist_match_length": 15}
		},
		"ui": {
			"colors": {"active_line": "green", "inactive_line": "white", "error": "red"},
			"scroll_timeout": 2,
			"refresh_interval_ms": 50,
			"wrap_width_percent": 90
		}
	}
	for file in config_files:
		if os.path.exists(file):
			try:
				with open(file) as f:
					file_config = json.load(f).get("config", {})
					if "global" in file_config and "logs_dir" not in file_config["global"]:
						file_config["global"]["logs_dir"] = "logs"
					for key in file_config:
						if key in default_config:
							default_config[key].update(file_config[key])
						else:
							default_config[key] = file_config[key]
				print(f"Successfully loaded and merged config from {file}")
				break 
			except Exception as e:
				pass
		else:
			pass


	def resolve(item):
		if isinstance(item, dict) and "env" in item:
			return os.environ.get(item["env"], item.get("default"))
		return item

	for section in ["mpd"]:
		for key in default_config["player"][section]:
			default_config["player"][section][key] = resolve(default_config["player"][section][key])
	
	for key in default_config["redis"]:
		default_config["redis"][key] = resolve(default_config["redis"][key])

	default_config["global"]["enable_debug"] = resolve(default_config["global"]["enable_debug"]) == "1"

	return default_config

CONFIG = load_config()

# ==============
#  INITIALIZATION
# ==============
LOG_DIR = CONFIG["global"]["logs_dir"]
try:
    created = not os.path.exists("logs")
    os.makedirs("logs", exist_ok=True)
    if created:
        print(f"Directory 'logs' created at: {os.path.abspath('logs')}")

except Exception as e:
    print(f"CRITICAL ERROR: Failed to create logs directory - {str(e)}")
    raise SystemExit(1)

if not os.path.exists("logs"):
    print("FATAL: 'logs' directory missing after creation attempt")
    raise SystemExit(1)

LYRICS_TIMEOUT_LOG = CONFIG["global"]["lyrics_timeout_log"]
DEBUG_LOG = CONFIG["global"]["debug_log"]
LOG_RETENTION_DAYS = CONFIG["global"]["log_retention_days"]
MAX_DEBUG_COUNT = CONFIG["global"]["max_debug_count"]

ENABLE_DEBUG_LOGGING = CONFIG["global"]["enable_debug"]
if ENABLE_DEBUG_LOGGING:
    debug_msg = "Debug logging ENABLED"
    print(debug_msg)
    print("=== Application started ===")
    print(f"Loaded config: {json.dumps(CONFIG, indent=2)}")
# else:
    # print("Debug logging DISABLED")

# Redis connection
REDIS_ENABLED = CONFIG["redis"]["enabled"]
redis_client = None
if REDIS_ENABLED:
    try:
        redis_client = redis.Redis(
            host=CONFIG["redis"]["host"],
            port=CONFIG["redis"]["port"],
            decode_responses=True
        )
        redis_client.ping()
    except Exception as e:
        REDIS_ENABLED = False

# Player configuration
MPD_HOST = CONFIG["player"]["mpd"]["host"] 
MPD_PORT = CONFIG["player"]["mpd"]["port"] 
MPD_PASSWORD = CONFIG["player"]["mpd"]["password"] 
MPD_TIMEOUT = CONFIG["player"]["mpd"]["timeout"]
PRIORITIZE_CMUS = CONFIG["player"]["prioritize_cmus"]

# Lyrics configuration
LYRIC_EXTENSIONS = CONFIG["lyrics"]["local_extensions"]
LYRIC_CACHE_DIR = CONFIG["lyrics"]["cache_dir"]
SEARCH_TIMEOUT = CONFIG["lyrics"]["search_timeout"]
VALIDATION_LENGTHS = CONFIG["lyrics"]["validation"]

# UI configuration
COLOR_MAP = {
    "black": curses.COLOR_BLACK,
    "blue": curses.COLOR_BLUE,
    "cyan": curses.COLOR_CYAN,
    "green": curses.COLOR_GREEN,
    "magenta": curses.COLOR_MAGENTA,
    "red": curses.COLOR_RED,
    "white": curses.COLOR_WHITE,
    "yellow": curses.COLOR_YELLOW
}


SCROLL_TIMEOUT = CONFIG["ui"]["scroll_timeout"]
REFRESH_INTERVAL = CONFIG["ui"]["refresh_interval_ms"]
WRAP_WIDTH_PERCENT = CONFIG["ui"]["wrap_width_percent"]

# Status system
MESSAGES = CONFIG["status_messages"]
TERMINAL_STATES = set(CONFIG["terminal_states"])
fetch_status_lock = threading.Lock()
fetch_status = {
	"current_step": None,
	"start_time": None,
	"lyric_count": 0,
	"done_time": None
}

TERMINAL_STATES = {'done', 'instrumental', 'time_out', 'failed', 'mpd', 'clear','cmus'} 

def update_fetch_status(step, lyrics_found=0):
	with fetch_status_lock:
		fetch_status.update({
			'current_step': step,
			'lyric_count': lyrics_found,
			'start_time': time.time() if step == 'start' else fetch_status['start_time'],
			'done_time': time.time() if step in TERMINAL_STATES else None
		})

def get_current_status(e=None, current_e=None):
	"""Return a formatted status message"""
	current_e = e
	with fetch_status_lock:
		if current_e != e:
			return e
		
		step = fetch_status['current_step']
		if not step:
			return None
		

		if step in TERMINAL_STATES and fetch_status['done_time']:
			if time.time() - fetch_status['done_time'] > 2:
				return ""

		if step == 'clear':
			return ""

		base_msg = MESSAGES.get(step, step)
		if fetch_status['start_time'] and step != 'done':
			end_time = fetch_status['done_time'] or time.time()
			elapsed = end_time - fetch_status['start_time']
			return f"{base_msg} {elapsed:.1f}s"
		
		return base_msg
		

# ================
#  ASYNC HELPERS
# ================
async def fetch_lrclib_async(artist, title, duration=None, session=None):
	"""Async version of LRCLIB fetch using aiohttp"""
	base_url = "https://lrclib.net/api/get"
	params = {'artist_name': artist, 'track_name': title}
	if duration:
		params['duration'] = duration

	try:
		async with (session or aiohttp.ClientSession()) as s:
			async with s.get(base_url, params=params) as response:
				if response.status == 200:
					try:
						data = await response.json(content_type=None)
						if data.get('instrumental', False):
							return None, None
						return data.get('syncedLyrics') or data.get('plainLyrics'), bool(data.get('syncedLyrics'))
					except aiohttp.ContentTypeError:
						log_debug("LRCLIB async error: Invalid JSON response")
				else:
					log_debug(f"LRCLIB async error: HTTP {response.status}")
	except aiohttp.ClientError as e:
		log_debug(f"LRCLIB async error: {e}")
	
	return None, None


# ================
#  LOGGING SYSTEM
# ================
def clean_debug_log():
	"""Maintain debug log size by keeping only last 100 entries"""
	log_dir = os.path.join(os.getcwd(), "logs")
	log_path = os.path.join(LOG_DIR, DEBUG_LOG)
	
	if not os.path.exists(log_path):
		return

	try:
		with open(log_path, 'r', encoding='utf-8') as f:
			lines = f.readlines()
		
		if len(lines) > MAX_DEBUG_COUNT:
			with open(log_path, 'w', encoding='utf-8') as f:
				f.writelines(lines[-MAX_DEBUG_COUNT:])
				
	except Exception as e:
		log_debug(f"Error cleaning debug log: {e}")

def log_debug(message):
    """Conditionally log debug messages to file"""
    if not ENABLE_DEBUG_LOGGING:
        return

    log_dir = os.path.join(os.getcwd(), LOG_DIR) 
    log_path = os.path.join(log_dir, DEBUG_LOG)
    
    # Verify paths
    print(f"Attempting to log to: {log_path}") 
    
    try:
        # Force directory check
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            print(f"Recreated logs directory at: {log_dir}")

        # Write test entry
        with open(log_path, 'a', encoding='utf-8') as f:
            f.flush()
            
    except Exception as e:
        print(f"LOG WRITE FAILURE: {str(e)}")


def log_debug(message):
	"""Conditionally log debug messages to file"""
	if not ENABLE_DEBUG_LOGGING:
		return

	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(log_dir, exist_ok=True)
	
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | {message}\n"
	
	try:
		with open(os.path.join(log_dir, DEBUG_LOG), 'a', encoding='utf-8') as f:
			f.write(log_entry)
		clean_debug_log()
	except Exception as e:
		pass 

def log_timeout(artist, title):
	"""Record failed lyric lookup with duplicate prevention"""
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
	
	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(LOG_DIR, exist_ok=True)
	log_path = os.path.join(LOG_DIR, LYRICS_TIMEOUT_LOG)

	# Check for existing entry
	entry_exists = False
	if os.path.exists(log_path):
		search_artist = artist or 'Unknown'
		search_title = title or 'Unknown'
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if (
					f"Artist: {search_artist}" in line and 
					f"Title: {search_title}" in line
				):
					entry_exists = True
					break

	# Add new entry if unique
	if not entry_exists:
		try:
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			clean_old_timeouts()
		except Exception as e:
			log_debug(f"Failed to write timeout log: {e}")

# ======================
#  CORE LYRIC FUNCTIONS
# ======================
def sanitize_filename(name):
	"""Make strings safe for filenames"""
	return re.sub(r'[<>:"/\\|?*]', '_', name)

def sanitize_string(s):
	"""Normalize strings for comparison"""
	return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

def fetch_lyrics_lrclib(artist_name, track_name, duration=None):
	"""Sync wrapper for async LRCLIB fetch"""
	try:
		return asyncio.run(fetch_lrclib_async(artist_name, track_name, duration))
	except Exception as e:
		log_debug(f"LRCLIB sync error: {e}")
		return None, None

# def parse_lrc_tags(lyrics):
	# """Extract metadata tags from LRC lyrics"""
	# tags = {}
	# for line in lyrics.split('\n'):
		# match = re.match(r'^\[(ti|ar|al):(.+)\]$', line, re.IGNORECASE)
		# if match:
			# key = match.group(1).lower()
			# value = match.group(2).strip()
			# tags[key] = value
	# return tags

def validate_lyrics(content, artist, title):
	"""Basic validation that lyrics match track"""
	# Check for timing markers
	if re.search(r'\[\d+:\d+\.\d+\]', content):
		return True
		
	# Check for instrumental markers
	if re.search(r'\b(instrumental)\b', content, re.IGNORECASE):
		return True

	# Normalize strings for comparison
	def normalize(s):
		return re.sub(r'[^\w]', '', str(s)).lower().replace(' ', '')[:15]

	norm_title = normalize(title)[:15]
	norm_artist = normalize(artist)[:15] if artist else ''
	norm_content = normalize(content)

	# Verify title/artist presence in lyrics
	return (norm_title in norm_content if norm_title else True) or \
		   (norm_artist in norm_content if norm_artist else True)

def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None, timeout=15):
	"""Fetch lyrics using syncedlyrics with a fallback"""
	try:
		def worker(result_dict, search_term, synced=True):
			"""Async worker for lyric search"""
			try:
				result = syncedlyrics.search(search_term) if synced else \
						 syncedlyrics.search(search_term, plain_only=True)
				result_dict["lyrics"] = result
				result_dict["synced"] = synced
			except Exception as e:
				log_debug(f"Lyrics search error: {e}")
				result_dict["lyrics"] = None
				result_dict["synced"] = False

		search_term = f"{track_name} {artist_name}".strip()
		if not search_term:
			log_debug("Empty search term")
			return None, None

		# Shared dictionary for results
		manager = multiprocessing.Manager()
		result_dict = manager.dict()

		# Fetch synced lyrics first
		process = multiprocessing.Process(target=worker, args=(result_dict, search_term, True))
		process.start()
		process.join(timeout)

		lyrics, is_synced = result_dict.get("lyrics"), result_dict.get("synced", False)

		# Check if lyrics are valid
		if lyrics and validate_lyrics(lyrics, artist_name, track_name):
			if is_synced and re.search(r'^\[\d+:\d+\.\d+\]', lyrics, re.MULTILINE):
				return lyrics, True
			else:
				return lyrics, False

		# Cleanup in case of timeout
		if process.is_alive():
			process.terminate()
			process.join()
			log_debug("Synced lyrics search timed out")

		# Fallback to plain lyrics
		log_debug("Attempting plain lyrics after synced failed")
		process = multiprocessing.Process(target=worker, args=(result_dict, search_term, False))
		process.start()
		process.join(timeout)

		lyrics = result_dict.get("lyrics")

		if lyrics and validate_lyrics(lyrics, artist_name, track_name):
			return lyrics, False

		# Cleanup in case of timeout
		if process.is_alive():
			process.terminate()
			process.join()
			log_debug("Plain lyrics search timed out")

		return None, None
	except Exception as e:
		log_debug(f"Lyrics fetch error: {e}")
		return None, None


def save_lyrics(lyrics, track_name, artist_name, extension):
	"""Save lyrics to appropriate file format"""
	folder = os.path.join(os.getcwd(), "synced_lyrics")
	os.makedirs(folder, exist_ok=True)
	
	# Generate safe filename
	sanitized_track = sanitize_filename(track_name)
	sanitized_artist = sanitize_filename(artist_name)
	filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
	file_path = os.path.join("synced_lyrics", filename)
	
	try:
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(lyrics)
		return file_path
	except Exception as e:
		log_debug(f"Lyric save error: {e}")
		return None

def get_cmus_info():
	"""Get current playback info from cmus"""
	try:
		output = subprocess.run(['cmus-remote', '-Q'], 
							   capture_output=True, 
							   text=True, 
							   check=True).stdout.splitlines()
	except subprocess.CalledProcessError:
		return None, 0, None, None, 0, "stopped"

	# Parse cmus output
	data = {
		"file": None,
		"position": 0,
		"artist": None,
		"title": None,
		"duration": 0,
		"status": "stopped",
		"tags": {}
	}

	for line in output:
		if line.startswith("file "):
			data["file"] = line[5:].strip()
		elif line.startswith("status "):
			data["status"] = line[7:].strip()
		elif line.startswith("position "):
			data["position"] = int(line[9:].strip())
		elif line.startswith("duration "):
			data["duration"] = int(line[9:].strip())
		elif line.startswith("tag "):
			parts = line.split(" ", 2)
			if len(parts) == 3:
				tag_name, tag_value = parts[1], parts[2].strip()
				data["tags"][tag_name] = tag_value

	data["artist"] = data["tags"].get("artist")
	data["title"] = data["tags"].get("title")

	return (data["file"], data["position"], data["artist"], 
			data["title"], data["duration"], data["status"])

def is_lyrics_timed_out(artist_name, track_name):
	"""Check if track is in timeout log"""
	log_path = os.path.join("logs", LYRICS_TIMEOUT_LOG)

	if not os.path.exists(log_path):
		return False

	try:
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if artist_name and track_name:
					if f"Artist: {artist_name}" in line and f"Title: {track_name}" in line:
						return True
		return False
	except Exception as e:
		log_debug(f"Timeout check error: {e}")
		return False

def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	"""Locate or fetch lyrics for current track"""
	update_fetch_status('local')
	base_name, _ = os.path.splitext(os.path.basename(audio_file))
	
	local_files = [
		(os.path.join(directory, f"{base_name}.a2"), 'a2'),
		(os.path.join(directory, f"{base_name}.lrc"), 'lrc'),
		(os.path.join(directory, f"{base_name}.txt"), 'txt')
	]

	# Validate existing files
	for file_path, ext in local_files:
		if os.path.exists(file_path):
			try:
				with open(file_path, 'r', encoding='utf-8') as f:
					content = f.read()
				
				if validate_lyrics(content, artist_name, track_name):
					log_debug(f"Validated local {ext} file")
					return file_path
				else:
					log_debug(f"Using unvalidated local {ext} file")
					return file_path
			except Exception as e:
				log_debug(f"File read error: {file_path} - {e}")
				continue

	# Handle instrumental tracks
	is_instrumental = (
		"instrumental" in track_name.lower() or 
		(artist_name and "instrumental" in artist_name.lower())
	)
	
	sanitized_track = sanitize_filename(track_name)
	sanitized_artist = sanitize_filename(artist_name)
	possible_filenames = [
		f"{sanitized_track}.a2",
		f"{sanitized_track}.lrc",
		f"{sanitized_track}.txt",
		f"{sanitized_track}_{sanitized_artist}.a2",
		f"{sanitized_track}_{sanitized_artist}.lrc",
		f"{sanitized_track}_{sanitized_artist}.txt"
	]

	synced_dir = os.path.join(os.getcwd(), "synced_lyrics")

	for dir_path in [directory, synced_dir]:
		for filename in possible_filenames:
			file_path = os.path.join(dir_path, filename)
			if os.path.exists(file_path):
				try:
					with open(file_path, 'r', encoding='utf-8') as f:
						content = f.read()
					if validate_lyrics(content, artist_name, track_name):
						log_debug(f"Using validated file: {file_path}")
						return file_path
					else:
						log_debug(f"Skipping invalid file: {file_path}")
				except Exception as e:
					log_debug(f"Error reading {file_path}: {e}")
					continue
	
	if is_instrumental:
		log_debug("Instrumental track detected")
		update_fetch_status('instrumental')
		return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

	
	# Check timeout status
	if is_lyrics_timed_out(artist_name, track_name):
		update_fetch_status('time_out')
		log_debug(f"Lyrics timeout active for {artist_name} - {track_name}")
		return None
	
	update_fetch_status('synced')
	# Fetch from syncedlyrics
	log_debug("Fetching from syncedlyrics...")
	fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	if fetched_lyrics:
		# Add validation warning if needed
		if not validate_lyrics(fetched_lyrics, artist_name, track_name):
			log_debug("Validation warning - possible mismatch")
			fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics
		
		# Determine file format
		is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) 
						for line in fetched_lyrics.split('\n'))
		extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	# Fallback to LRCLIB
	update_fetch_status("lrc_lib")
	log_debug("Fetching from LRCLIB...")
	fetched_lyrics, is_synced = fetch_lyrics_lrclib(artist_name, track_name, duration)
	if fetched_lyrics:
		extension = 'lrc' if is_synced else 'txt'
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	log_debug("No lyrics found from any source")
	update_fetch_status("failed")
	log_timeout(artist_name, track_name)
	return None

def parse_time_to_seconds(time_str):
	"""Convert LRC timestamp to seconds"""
	try:
		minutes, rest = time_str.split(':', 1)
		seconds, milliseconds = rest.split('.', 1)
		return int(minutes)*60 + int(seconds) + float(f"0.{milliseconds}")
	except ValueError:
		return 0

def load_lyrics(file_path):
	"""Parse lyric file into time-text pairs"""
	lyrics = []
	errors = []
	
	try:
		with open(file_path, 'r', encoding="utf-8") as f:
			lines = f.readlines()
	except Exception as e:
		errors.append(f"File open error: {str(e)}")
		return lyrics, errors

	# A2 Format Parsing
	if file_path.endswith('.a2'):
		current_line = []
		line_pattern = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
		word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')

		for line in lines:
			line = line.strip()
			if not line:
				continue

			# Parse line timing
			line_match = line_pattern.match(line)
			if line_match:
				line_time = parse_time_to_seconds(line_match.group(1))
				lyrics.append((line_time, None))
				content = line_match.group(2)
				
				# Parse word-level timing
				words = word_pattern.findall(content)
				for start_str, text, end_str in words:
					start = parse_time_to_seconds(start_str)
					end = parse_time_to_seconds(end_str)
					clean_text = re.sub(r'<.*?>', '', text).strip()
					if clean_text:
						lyrics.append((start, (clean_text, end)))
				
				# Handle remaining text
				remaining = re.sub(word_pattern, '', content).strip()
				if remaining:
					lyrics.append((line_time, (remaining, line_time)))
				lyrics.append((line_time, None))

	# Plain Text Format
	elif file_path.endswith('.txt'):
		for line in lines:
			raw_line = line.rstrip('\n')
			lyrics.append((None, raw_line))
	# LRC Format
	else:
		for line in lines:
			raw_line = line.rstrip('\n')
			line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
			if line_match:
				line_time = parse_time_to_seconds(line_match.group(1))
				lyric_content = line_match.group(2).strip()
				lyrics.append((line_time, lyric_content))
			else:
				lyrics.append((None, raw_line))

	return lyrics, errors

# ==============
#  PLAYER DETECTION
# ==============
def get_player_info():
	"""Detect active player (CMUS or MPD)"""
	# Try CMUS first
	cmus_info = get_cmus_info()
	if cmus_info[0] is not None:
		return 'cmus', cmus_info
	
	# Fallback to MPD
	try:
		mpd_info = get_mpd_info()
		if mpd_info[0] is not None:
			return 'mpd', mpd_info
	except (base.ConnectionError, socket.error) as e:
		log_debug(f"MPD connection error: {str(e)}")
	except base.CommandError as e:
		log_debug(f"MPD command error: {str(e)}")
	except Exception as e:
		log_debug(f"Unexpected MPD error: {str(e)}")

	return None, (None, 0, None, None, 0, "stopped")

def get_mpd_info():
	"""Get current playback info from MPD, handling password authentication."""
	client = MPDClient()
	client.timeout = MPD_TIMEOUT

	try:
		client.connect(MPD_HOST, MPD_PORT)
		
		# Authenticate if a password is set
		if MPD_PASSWORD:
			client.password(MPD_PASSWORD)
		
		status = client.status()
		current_song = client.currentsong()

		# Ensure artist is always a string (handle lists)
		artist = current_song.get("artist", None)
		if isinstance(artist, list):
			artist = ", ".join(artist)  # Convert list to comma-separated string
		
		data = {
			"file": current_song.get("file", ""),
			"position": float(status.get("elapsed", 0)),
			"artist": artist,
			"title": current_song.get("title", None),
			"duration": float(status.get("duration", status.get("time", 0))),
			"status": status.get("state", "stopped")
		}

		client.close()
		client.disconnect()

		return (data["file"], data["position"], data["artist"], 
				data["title"], data["duration"], data["status"])

	except (socket.error, ConnectionRefusedError) as e:
		log_debug(f"MPD connection error: {str(e)}")
	except Exception as e:
		log_debug(f"Unexpected MPD error: {str(e)}")

	update_fetch_status("mpd")
	return (None, 0, None, None, 0, "stopped")


# ==============
#  UI RENDERING
# ==============
def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, 
				  is_txt_format, is_a2_format, current_idx, use_manual_offset, 
				  time_adjust=0, is_fetching=False):
	"""Render lyrics in curses interface"""
	height, width = stdscr.getmaxyx()
	start_screen_line = 0
	
	status_msg = get_current_status()

	# A2 Format Display
	if is_a2_format:
		a2_lines = []
		current_line = []
		# Build line structure
		for t, item in lyrics:
			if item is None:
				if current_line:
					a2_lines.append(current_line)
					current_line = []
			else:
				current_line.append((t, item))

		# Find active words
		active_line_idx = -1
		active_words = []
		for line_idx, line in enumerate(a2_lines):
			line_active = []
			for word_idx, (start, (text, end)) in enumerate(line):
				if start <= position < end:
					line_active.append(word_idx)
					active_line_idx = line_idx
			if line_active:
				active_words = line_active

		# Calculate visible range
		stdscr.clear()
		current_y = 1
		visible_lines = height - 2
		start_line = max(0, active_line_idx - visible_lines // 2)
		
		# Render lines
		for line_idx in range(start_line, min(start_line + visible_lines, len(a2_lines))):
			if current_y >= height - 1:
				break

			line = a2_lines[line_idx]
			line_str = " ".join([text for _, (text, _) in line])
			x_pos = max(0, (width - len(line_str)) // 2)
			x_pos = min(x_pos, width - 1)
			
			# Render individual words
			cursor = 0
			for word_idx, (start, (text, end)) in enumerate(line):
				remaining_width = width - x_pos - cursor - 1
				if remaining_width <= 0:
					break
				display_text = text[:remaining_width]
				color = curses.color_pair(2) if line_idx == active_line_idx and word_idx in active_words else curses.color_pair(3)
				
				try:
					if x_pos + cursor < width:
						stdscr.addstr(current_y, x_pos + cursor, display_text, color)
						cursor += len(display_text) + 1
				except curses.error:
					break
			current_y += 1
			

	# Standard Text/LRC Display
	else:
		available_lines = height - 3
		wrap_width = width - 2
		wrapped_lines = []
		
		# Wrap text for display
		for orig_idx, (_, lyric) in enumerate(lyrics):
			if lyric.strip():
				lines = textwrap.wrap(lyric, wrap_width, drop_whitespace=False)
				if lines:
					wrapped_lines.append((orig_idx, lines[0]))
					for line in lines[1:]:
						wrapped_lines.append((orig_idx, " " + line))
			else:
				wrapped_lines.append((orig_idx, ""))
		
		# Calculate scroll position
		total_wrapped = len(wrapped_lines)
		max_start = max(0, total_wrapped - available_lines)
		
		if use_manual_offset:
			start_screen_line = max(0, min(manual_offset, max_start))
		else:
			indices = [i for i, (orig, _) in enumerate(wrapped_lines) if orig == current_idx]
			if indices:
				center = (indices[0] + indices[-1]) // 2
			else:
				center = current_idx  # Default to current_idx if no wrapped line matches

			ideal_start = center - (available_lines // 2)
			start_screen_line = max(0, min(ideal_start, max_start))


		# Render visible lines
		end_screen_line = start_screen_line + available_lines
		stdscr.clear()
		current_line_y = 1
		for idx, (orig_idx, line) in enumerate(wrapped_lines[start_screen_line:end_screen_line]):
			if current_line_y >= height - 1:
				break
			trimmed_line = line.strip()
			padding = max(0, (width - len(trimmed_line)) // 2)
			centered_line = " " * padding + trimmed_line
			color = curses.color_pair(2) if orig_idx == current_idx else curses.color_pair(3)
			
			try:
				stdscr.addstr(current_line_y, 0, centered_line, color)
			except curses.error:
				pass
			current_line_y += 1

		if status_msg:
			try:
				y = height - 1
				x = max(0, (width - len(status_msg)) // 2)
				stdscr.addstr(y, x, status_msg)
			except curses.error:
				pass
				
		# Show time adjustment
		if time_adjust != 0:
			offset_str = f" Offset: {time_adjust:+.1f}s "
			offset_str = offset_str[:width-1]
			try:
				color = curses.color_pair(2) if time_adjust != 0 else curses.color_pair(3)
				stdscr.addstr(height-2, width-len(offset_str)-1, offset_str, color | curses.A_BOLD)
			except curses.error:
				pass

		# Status line
		status_line = f"Line {current_idx+1}/{len(lyrics)}"
		if time_adjust != 0:
			status_line += "[Adj]"
		status_line = status_line[:width-1]
		
		if height > 1:
			try:
				stdscr.addstr(height-1, 0, status_line, curses.A_BOLD)
			except curses.error:
				pass
	stdscr.refresh()
	return start_screen_line

# ================
#  INPUT HANDLING
# ================
def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw, time_adjust):
	"""Process user input events"""
	if key == ord('r') or key == ord('R'):
		return False, manual_offset, last_input_time, needs_redraw, time_adjust
	elif key == curses.KEY_UP:
		manual_offset = max(0, manual_offset - 1)
		last_input_time = time.time()
		needs_redraw = True
	elif key == curses.KEY_DOWN:
		manual_offset += 1
		last_input_time = time.time()
		needs_redraw = True
	elif key == curses.KEY_RESIZE:
		needs_redraw = True
	return True, manual_offset, last_input_time, needs_redraw, time_adjust

def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, 
				  is_txt_format, is_a2_format, current_idx, manual_scroll_active, 
				  time_adjust=0, is_fetching=False):
	"""Update display based on current state"""
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, 
							 os.path.basename(audio_file), manual_offset, 
							 is_txt_format, is_a2_format, current_idx, True, time_adjust, is_fetching)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, 
							os.path.basename(audio_file), manual_offset, 
							is_txt_format, is_a2_format, current_idx, 
							manual_scroll_active, time_adjust, is_fetching)

# Global executor for non-blocking lyric fetching
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
future_lyrics = None  # Holds the async result

def fetch_lyrics_async(audio_file, directory, artist, title, duration):
	"""Function to fetch lyrics in a separate thread"""
	try:
		lyrics_file = find_lyrics_file(audio_file, directory, artist, title, duration)
		if lyrics_file:
			is_txt_format = lyrics_file.endswith('.txt')
			is_a2_format = lyrics_file.endswith('.a2')
			lyrics, errors = load_lyrics(lyrics_file)
			update_fetch_status('done', len(lyrics))
			return (lyrics, errors), is_txt_format, is_a2_format
		update_fetch_status('failed')
		return ([], []), False, False
	except Exception as e:
		log_debug(f"Async fetch error: {e}")
		update_fetch_status('failed')
		return ([], []), False, False

# def clean_lyrics(raw_lyrics):
	# """Ensure lyrics have valid timestamps."""
	# cleaned = []
	# last_valid = 0.0
	# for t, text in raw_lyrics:
		# if t is None:
			# cleaned_t = last_valid  # Use last valid timestamp
		# else:
			# cleaned_t = max(0.0, float(t))
			# last_valid = cleaned_t
		# cleaned.append((cleaned_t, text))
	# return cleaned

def sync_player_position(status, raw_position, last_position_time, time_adjust, duration):
	"""Synchronize player position with system clock"""
	current_time = time.time()
	elapsed = current_time - last_position_time
	
	if status == "playing":
		estimated = raw_position + elapsed + time_adjust
	else:  # paused/stopped
		estimated = raw_position + time_adjust
	
	# Apply bounds
	estimated = max(0.0, min(estimated, duration))
	return estimated, current_time

def main(stdscr):
	curses.start_color()
	curses.init_pair(2, COLOR_MAP[CONFIG["ui"]["colors"]["active_line"]], curses.COLOR_BLACK)
	curses.init_pair(3, COLOR_MAP[CONFIG["ui"]["colors"]["inactive_line"]], curses.COLOR_BLACK)
	curses.curs_set(0)
	stdscr.timeout(REFRESH_INTERVAL)

	state = {
		'current_file': None,
		'lyrics': [],
		'errors': [],
		'manual_offset': 0,
		'last_input': 0,
		'time_adjust': 0.0,
		'last_raw_pos': 0.0,
		'last_pos_time': time.time(),
		'timestamps': [],
		'valid_indices': [],
		'paused': False,
		'last_idx': -1,
		'last_manual': False,
		'force_redraw': False
	}
	
	executor = ThreadPoolExecutor(max_workers=2)
	future_lyrics = None

	while True:
		try:
			player_type, (audio_file, raw_pos, artist, title, duration, status) = get_player_info()
			raw_position = float(raw_pos or 0)
			duration = float(duration or 0)
			now = time.time()

			if audio_file != state['current_file']:
				state.update({
					'current_file': audio_file,
					'lyrics': [],
					'errors': [],
					'manual_offset': 0,
					'last_raw_pos': raw_position,
					'last_pos_time': now,
					'last_idx': -1,
					'force_redraw': True
				})
				if audio_file:
					future_lyrics = executor.submit(
						fetch_lyrics_async, audio_file,
						os.path.dirname(audio_file) if player_type == 'cmus' else "",
						artist or "Unknown", title or os.path.basename(audio_file), duration
					)

			if future_lyrics and future_lyrics.done():
				try:
					(new_lyrics, errors), is_txt, is_a2 = future_lyrics.result()
					state.update({
						'lyrics': new_lyrics,
						'errors': errors,
						'timestamps': sorted([t for t, _ in new_lyrics if t is not None]),
						'valid_indices': [i for i, (t, _) in enumerate(new_lyrics) if t is not None],
						'last_idx': -1,
						'force_redraw': True
					})
					future_lyrics = None
				except Exception as e:
					state.update({'errors': [f"Lyric load error: {str(e)}"], 'force_redraw': True})
					future_lyrics = None

			estimated_pos, new_pos_time = sync_player_position(
				status, raw_position, state['last_pos_time'], state['time_adjust'], duration
			)
			state.update({'last_raw_pos': raw_position, 'last_pos_time': new_pos_time})

			current_idx = bisect.bisect_right(state['timestamps'], estimated_pos + 0.001) - 1
			current_idx = max(0, min(current_idx, len(state['timestamps']) - 1)) if state['timestamps'] else 0


			highlight_changed = current_idx != state['last_idx']
			state['last_idx'] = current_idx

			key = stdscr.getch()
			if key == ord('q'):
				break

			if key != -1:
				running, new_offset, last_input, input_redraw, new_adjust = handle_scroll_input(
					key, state['manual_offset'], state['last_input'], False, state['time_adjust']
				)
				state.update({
					'manual_offset': new_offset,
					'last_input': last_input,
					'time_adjust': new_adjust,
					'force_redraw': True
				})

			manual_scroll = (time.time() - state['last_input']) < SCROLL_TIMEOUT
			if manual_scroll != state['last_manual']:
				state.update({'last_manual': manual_scroll, 'force_redraw': True})

			if any([
				state['force_redraw'],
				highlight_changed,
				len(state['errors']) > 0,
				future_lyrics and future_lyrics.done()
			]):
				update_display(
					stdscr, state['lyrics'], state['errors'], estimated_pos, state['current_file'],
					state['manual_offset'], False, False, current_idx, manual_scroll,
					state['time_adjust'], future_lyrics is not None
				)
				state['force_redraw'] = False
				stdscr.noutrefresh()
				curses.doupdate()

			if status == "paused":
				time.sleep(0.1)
			elif not manual_scroll and not highlight_changed:
				time.sleep(0.02)

		except Exception as e:
			log_debug(f"Main loop error: {str(e)}")

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(main)
		except KeyboardInterrupt:
			break
		except Exception as e:
			log_debug(f"Fatal error: {str(e)}")
			time.sleep(1)
			continue

			continue
