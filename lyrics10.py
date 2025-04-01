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

# log_trace("Starting request processing")
# log_debug(f"Received payload: {payload}")
# log_info("API request received")
# log_warn("Slow database response")
# log_error("Failed to update record")
# log_fatal("Unrecoverable database error")

# ==============
#  GLOBALS
# ==============
sync_results = {
	'bisect_index': 0,
	'proximity_index': 0,
	'lock': threading.Lock()
}
LOG_LEVELS = {
	"FATAL": 5,
	"ERROR": 4,
	"WARN": 3,
	"INFO": 2,
	"DEBUG": 1,
	"TRACE": 0
}
# ==============
#  CONFIGURATION
# ==============
config_files = ["config.json", "config1.json", "config2.json"]

def load_config():
	"""Load and merge configuration from file and environment"""
	default_config = {
		"global": {
			"logs_dir": "logs",
			"log_file": "application.log",
			"log_level": "INFO",
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
			"wrap_width_percent": 90,
			"bisect_offset": 0.01,  # Only used for bisect method
			"proximity_threshold": 0.1  # Only used for proximity method (50ms)
		}
	}

	for file in config_files:
		if os.path.exists(file):
			try:
				with open(file) as f:
					file_config = json.load(f).get("config", {})
					if "global" in file_config and "logs_dir" not in file_config["global"]:
						file_config["global"]["logs_dir"] = "logs"
					# Merge the file configuration with the default configuration
					for key in file_config:
						if key in default_config:
							# If the key exists in the default config, update it
							default_config[key].update(file_config[key])
						else:
							# If the key doesn't exist, add it to the default config
							default_config[key] = file_config[key]
				print(f"Successfully loaded and merged config from {file}")
				break  # Stop after the first valid config file is found
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
# Add debug startup message
if ENABLE_DEBUG_LOGGING:
	debug_msg = "Debug logging ENABLED"
	print(debug_msg)  # Confirm in console
	print("=== Application started ===")
	print(f"Loaded config: {json.dumps(CONFIG, indent=2)}")

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

TERMINAL_STATES = {'done', 'instrumental', 'time_out', 'failed', 'mpd', 'clear','cmus'}  # Ensure this is defined

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
		

		# Hide status after 2 seconds for terminal states
		if step in TERMINAL_STATES and fetch_status['done_time']:
			if time.time() - fetch_status['done_time'] > 2:
				return ""

		if step == 'clear':
			return ""

		# Return pre-defined message with elapsed time if applicable
		base_msg = MESSAGES.get(step, step)
		if fetch_status['start_time'] and step != 'done':
			# Use done_time if available for terminal states
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
		# Use existing session if provided, otherwise create one temporarily
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
		# Read existing log contents
		with open(log_path, 'r', encoding='utf-8') as f:
			lines = f.readlines()
		
		# Trim if over 100 lines
		if len(lines) > MAX_DEBUG_COUNT:
			with open(log_path, 'w', encoding='utf-8') as f:
				f.writelines(lines[-MAX_DEBUG_COUNT:])
				
	except Exception as e:
		log_debug(f"Error cleaning debug log: {e}")

def clean_log():
	"""Maintain log size by rotating files"""
	log_dir = os.path.join(os.getcwd(), CONFIG["global"]["logs_dir"])
	log_path = os.path.join(log_dir, CONFIG["global"]["log_file"])
	
	try:
		if os.path.exists(log_path):
			with open(log_path, "r+") as f:
				lines = f.readlines()
				if len(lines) > CONFIG["global"]["max_log_count"]:
					keep = lines[-CONFIG["global"]["max_log_count"]:]
					f.seek(0)
					f.truncate()
					f.writelines(keep)
	except Exception as e:
		print(f"Log cleanup failed: {str(e)}", file=sys.stderr)

def log_message(level: str, message: str):
	"""Unified logging function with level-based filtering and rotation"""
	# Get config values
	log_dir = os.path.join(os.getcwd(), CONFIG["global"]["logs_dir"])
	main_log = os.path.join(log_dir, CONFIG["global"]["log_file"])
	debug_log = os.path.join(log_dir, DEBUG_LOG)
	configured_level = LOG_LEVELS.get(CONFIG["global"]["log_level"], 2)
	message_level = LOG_LEVELS.get(level.upper(), 2)
	
	try:
		# Create log directory if needed
		os.makedirs(log_dir, exist_ok=True)
		# timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		#timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
		timestamp = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000000) % 1000000:06d}"
		
		# Always write to debug log if enabled and level <= DEBUG
		if CONFIG["global"]["enable_debug"] and message_level <= LOG_LEVELS["DEBUG"]:
			debug_entry = f"{timestamp} | {level.upper()} | {message}\n"
			with open(debug_log, "a", encoding="utf-8") as f:
				f.write(debug_entry)
			clean_debug_log()

		# Write to main log if message level >= configured level
		if message_level >= configured_level:
			main_entry = f"{timestamp} | {level.upper()} | {message}\n"
			with open(main_log, "a", encoding="utf-8") as f:
				f.write(main_entry)
			
			# Rotate main log if needed
			if os.path.getsize(main_log) > CONFIG["global"]["max_log_count"] * 1024:
				clean_log()

	except Exception as e:
		sys.stderr.write(f"Logging failed: {str(e)}\n")

# Specific level helpers
def log_fatal(message: str):
	log_message("FATAL", message)

def log_error(message: str):
	log_message("ERROR", message)

def log_warn(message: str):
	log_message("WARN", message)

def log_info(message: str):
	log_message("INFO", message)

def log_debug(message: str):
	log_message("DEBUG", message)

def log_trace(message: str):
	log_message("TRACE", message)


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
	"""Convert various timestamp formats to seconds with millisecond precision."""
	patterns = [
		r'^(?P<m>\d+):(?P<s>\d+\.\d+)$',  # MM:SS.ms
		r'^(?P<m>\d+):(?P<s>\d+):(?P<ms>\d{1,3})$',  # MM:SS:ms
		r'^(?P<m>\d+):(?P<s>\d+)$',  # MM:SS
		r'^(?P<s>\d+\.\d+)$',  # SS.ms
		r'^(?P<s>\d+)$'  # SS
	]
	
	for pattern in patterns:
		match = re.match(pattern, time_str)
		if match:
			parts = match.groupdict()
			minutes = int(parts.get('m', 0) or 0)
			seconds = float(parts.get('s', 0) or 0)
			milliseconds = int(parts.get('ms', 0) or 0) / 1000
			return round(minutes * 60 + seconds + milliseconds, 3)
	
	raise ValueError(f"Invalid time format: {time_str}")

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
				   time_adjust=0, is_fetching=False, subframe_fraction=0.0):
	"""Render lyrics in curses interface, with subframe_fraction added for smoother transitions."""
	height, width = stdscr.getmaxyx()
	start_screen_line = 0
	
	status_msg = get_current_status()
	
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
	elif key == ord('-'):
		time_adjust -= 0.1
		needs_redraw = True
	elif key == ord('=') or key == ord('+'):
		time_adjust += 0.1
		needs_redraw = True
	elif key == ord('0'):
		time_adjust = 0.0
		needs_redraw = True
	return True, manual_offset, last_input_time, needs_redraw, time_adjust

def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, 
				   is_txt_format, is_a2_format, current_idx, manual_scroll_active, 
				   time_adjust=0, is_fetching=False, subframe_fraction=0.0):
	"""Update display based on current state.
	
	Now includes subframe_fraction for fine-grained progress within a lyric line.
	"""
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  os.path.basename(audio_file), manual_offset, 
							  is_txt_format, is_a2_format, current_idx, True, 
							  time_adjust, is_fetching, subframe_fraction)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  os.path.basename(audio_file), manual_offset, 
							  is_txt_format, is_a2_format, current_idx, 
							  manual_scroll_active, time_adjust, is_fetching, subframe_fraction)


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


def sync_player_position(status, raw_pos, last_time, time_adjust, duration):
	now = time.perf_counter()
	elapsed = now - last_time
	
	if status == "playing":
		estimated = raw_pos + elapsed + time_adjust
	else:
		estimated = raw_pos + time_adjust
	
	return max(0.0, min(estimated, duration)), now

def find_current_lyric_index(position, timestamps):
	if not timestamps:
		return 0

	idx = bisect.bisect_left(timestamps, position)
	idx = max(0, min(idx, len(timestamps)-1))

	if idx+1 < len(timestamps):
		current_duration = timestamps[idx+1] - timestamps[idx]
		position_in_line = position - timestamps[idx]
		if current_duration > 0 and (position_in_line / current_duration) > 0.95:
			return idx + 1
	
	return idx

EPSILON = 1e-3  # Small constant to avoid division by zero

def bisect_worker(position, timestamps, offset):
	"""Returns the closest index using bisect based on a given offset."""
	if not timestamps:
		return None  # No timestamps available

	idx = bisect.bisect_right(timestamps, position + offset) - 1
	return max(0, min(idx, len(timestamps) - 1))

def proximity_worker(position, timestamps, threshold):
	"""Returns the closest index based on proximity and progress within the current line."""
	if not timestamps:
		return None  # No timestamps available

	idx = bisect.bisect_left(timestamps, position)
	idx = max(0, min(idx, len(timestamps) - 1))

	# Check if the next timestamp is close enough to switch early
	if idx + 1 < len(timestamps):
		current_duration = timestamps[idx + 1] - timestamps[idx]
		position_in_line = position - timestamps[idx]
		if current_duration > 0:
			progress_ratio = position_in_line / current_duration
			if progress_ratio > (1 - threshold):
				idx += 1
	return idx

def subframe_interpolation(continuous_position, timestamps, index):
	"""
	Given an index, compute a fraction (0.0 to 1.0) representing the progress between this timestamp and the next.
	This sub-frame fraction can be used for smoother UI transitions.
	"""
	if index < 0 or index >= len(timestamps) - 1:
		return index, 0.0
	start = timestamps[index]
	end = timestamps[index + 1]
	if end - start == 0:
		return index, 0.0
	fraction = (continuous_position - start) / (end - start)
	fraction = max(0.0, min(1.0, fraction))
	return index, fraction

def main(stdscr):
	# Initialize colors and UI
	curses.start_color()
	curses.init_pair(2, COLOR_MAP.get(CONFIG["ui"]["colors"]["active_line"], curses.COLOR_GREEN), curses.COLOR_BLACK)
	curses.init_pair(3, COLOR_MAP.get(CONFIG["ui"]["colors"]["inactive_line"], curses.COLOR_WHITE), curses.COLOR_BLACK)
	curses.curs_set(0)
	stdscr.timeout(80)  # More frequent updates (80ms)

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
		'force_redraw': False,
		'last_start_screen_line': 0,
		'is_txt': False,
		'is_a2': False,
		'window_size': stdscr.getmaxyx(),
		'manual_timeout_handled': True,
		'last_position': 0.0,
		'lyrics_loaded_time': None,
		'is_loading': False
	}

	executor = ThreadPoolExecutor(max_workers=4)
	future_lyrics = None

	# Playback tracking
	last_cmus_position = 0
	last_position_time = time.time()
	estimated_position = 0
	playback_paused = False

	while True:
		try:
			current_time = time.time()
			needs_redraw = False
			time_since_input = current_time - (state['last_input'] or 0)

			# Handle manual scroll timeout
			if state['last_input'] > 0:
				if time_since_input >= SCROLL_TIMEOUT:
					if not state['manual_timeout_handled']:
						needs_redraw = True
						state['manual_timeout_handled'] = True
					if time_since_input >= SCROLL_TIMEOUT + 0.1:
						state['last_input'] = 0
				else:
					state['manual_timeout_handled'] = False

			manual_scroll = state['last_input'] > 0 and time_since_input < SCROLL_TIMEOUT

			# Window resize handling
			current_window_size = stdscr.getmaxyx()
			if current_window_size != state['window_size']:
				old_h, _ = state['window_size']
				new_h, _ = current_window_size
				if state['lyrics']:
					state['manual_offset'] = int(state['manual_offset'] * (new_h / old_h))
					# state['manual_offset'] = max(0, min(len(state['lyrics']) - 1, 
						# state['manual_offset'] + (new_h - old_h) // 2))
				state['window_size'] = current_window_size
				needs_redraw = True
				start_screen_line = update_display(
					stdscr, 
					state['lyrics'], 
					state['errors'], 
					state['last_position'], 
					state['current_file'],
					state['manual_offset'], 
					state['is_txt'],
					state['is_a2'],
					state['last_idx'], 
					manual_scroll,
					state['time_adjust'], 
					future_lyrics is not None
				)
				state['last_start_screen_line'] = start_screen_line

			# Get playback info
			player_type, (audio_file, raw_pos, artist, title, duration, status) = get_player_info()
			raw_position = float(raw_pos or 0)
			duration = float(duration or 0)
			now = time.time()

			# Track change detection
			if audio_file != state['current_file']:
				state.update({
					'current_file': audio_file,
					'lyrics': [],
					'errors': [],
					'last_raw_pos': raw_position,
					'last_pos_time': now,
					'last_idx': -1,
					'force_redraw': True,
					'is_txt': False,
					'is_a2': False,
					'lyrics_loaded_time': None
				})
				if audio_file:
					future_lyrics = executor.submit(
						fetch_lyrics_async,
						audio_file,
						os.path.dirname(audio_file) if player_type == 'cmus' else "",
						artist or "Unknown",
						title or os.path.basename(audio_file),
						duration
					)

			# Handle loaded lyrics
			if future_lyrics and future_lyrics.done():
				try:
					(new_lyrics, errors), is_txt, is_a2 = future_lyrics.result()
					state.update({
						'lyrics': new_lyrics,
						'errors': errors,
						'timestamps': sorted([t for t, _ in new_lyrics if t is not None]) if not (is_txt or is_a2) else [],
						'valid_indices': [i for i, (t, _) in enumerate(new_lyrics) if t is not None],
						'last_idx': -1,
						'force_redraw': True,
						'is_txt': is_txt,
						'is_a2': is_a2,
						'lyrics_loaded_time': time.time()
					})
					future_lyrics = None
				except Exception as e:
					state.update({
						'errors': [f"Lyric load error: {str(e)}"],
						'force_redraw': True,
						'lyrics_loaded_time': time.time()
					})
					future_lyrics = None

			# Delayed force redraw
			if state['lyrics_loaded_time'] and time.time() - state['lyrics_loaded_time'] >= 2:
				state['force_redraw'] = True
				state['lyrics_loaded_time'] = None

			# Update position estimation
			if raw_position != last_cmus_position:
				last_cmus_position = raw_position
				last_position_time = now
				estimated_position = raw_position
				playback_paused = (status == "paused")

			if status == "playing" and not playback_paused:
				elapsed = now - last_position_time
				# estimated_position = last_cmus_position + elapsed
				estimated_position = last_cmus_position + (elapsed * 0.95)
				estimated_position = max(0, min(estimated_position, duration))
			elif status == "paused":
				estimated_position = raw_position
				last_position_time = now

			continuous_position = max(0, estimated_position + state['time_adjust'])
			continuous_position = min(continuous_position, duration)

			# Synchronization logic
			current_idx = -1
			if state['timestamps'] and not state['is_txt']:
				with ThreadPoolExecutor(max_workers=2) as sync_exec:
					bisect_future = sync_exec.submit(
						bisect_worker,
						continuous_position,
						state['timestamps'],
						CONFIG["ui"]["bisect_offset"]
					)
					proximity_future = sync_exec.submit(
						proximity_worker,
						continuous_position,
						state['timestamps'],
						CONFIG["ui"]["proximity_threshold"]
					)
					bisect_idx = bisect_future.result()
					proximity_idx = proximity_future.result()

				# Conflict resolution
				if abs(bisect_idx - proximity_idx) > 1:
					chosen_idx = bisect_idx
				else:
					chosen_idx = min(bisect_idx, proximity_idx)
				
				current_idx = max(-1, min(chosen_idx, len(state['timestamps']) - 1))
				
				# Timestamp validation
				if current_idx >= 0 and continuous_position < state['timestamps'][current_idx]:
					#compute_weighted_index
					current_idx = max(-1, current_idx - 1)
			else:
				current_idx = -1
				if (state['is_txt'] or state['is_a2']) and duration > 0 and len(state['lyrics']) > 0:
					time_per_line = duration / len(state['lyrics'])
					# Compute a fractional line number based on continuous playback position.
					continuous_line = continuous_position / time_per_line

					window_h = state['window_size'][0]
					if len(state['lyrics']) <= window_h:
						target_offset = 0
					else:
						# Center the view on the fractional current line.
						target_offset = continuous_line - (window_h / 2)
						# Round to the nearest integer to match curses window requirements.
						target_offset = int(round(target_offset))
						# Clamp target_offset within valid bounds.
						target_offset = max(0, min(target_offset, len(state['lyrics']) - window_h))
					
					if not manual_scroll:
						if target_offset != state['manual_offset']:
							state['manual_offset'] = target_offset
							needs_redraw = True

			# Update state
			highlight_changed = current_idx != state['last_idx']
			state['last_idx'] = current_idx

			# Input handling
			key = stdscr.getch()
			if key == ord('q'):
				break

			new_input = key != -1
			if new_input:
				cont, new_manual_offset, last_input, needs_redraw_input, new_time_adjust = handle_scroll_input(
					key, state['manual_offset'], state['last_input'], needs_redraw, state['time_adjust']
				)
				if manual_scroll:
					if state['lyrics']:
						state['manual_offset'] = max(0, min(new_manual_offset, len(state['lyrics']) - 1))
					else:
						state['manual_offset'] = new_manual_offset
				state['last_input'] = last_input
				state['time_adjust'] = new_time_adjust
				state['force_redraw'] = state['force_redraw'] or needs_redraw_input
				if not cont:
					break

			# Update display
			if (manual_scroll and new_input) or (not manual_scroll and (highlight_changed or needs_redraw or state['force_redraw'])):
				start_screen_line = update_display(
					stdscr, 
					state['lyrics'], 
					state['errors'], 
					continuous_position, 
					state['current_file'],
					state['manual_offset'], 
					state['is_txt'],
					state['is_a2'],
					current_idx if not manual_scroll else -1,
					manual_scroll,
					state['time_adjust'], 
					future_lyrics is not None
				)
				state.update({
					'force_redraw': False,
					'last_manual': manual_scroll,
					'last_start_screen_line': start_screen_line
				})
				needs_redraw = False

			# Pause handling
			if status == "paused":
				time.sleep(0.1)
			elif not manual_scroll:
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
			break
