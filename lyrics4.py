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
from datetime import datetime, timedelta  # Time handling for logs
from mpd import MPDClient  # MPD support
import socket # used for listening for common mpd port 6600

# ==============
#  GLOBAL CONFIG
# ==============
LYRICS_TIMEOUT_LOG = "lyrics_timeouts.log"  # Track failed lyric searches
ENABLE_DEBUG_LOGGING = os.environ.get('DEBUG') == '1'  # Debug flag
DEBUG_LOG = "debug.log"  # Debug output file
LOG_RETENTION_DAYS = 10  # Days to keep timeout logs of music files
MAX_DEBUG_COUNT = 100    # Maximum line count in debug log
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
# REDIS_TTL = 3600  # 1 hour cache
MPD_HOST = os.environ.get('MPD_HOST', 'localhost')
MPD_PORT = int(os.environ.get('MPD_PORT', 6600))
MPD_TIMEOUT = 10  # seconds
MPD_PASSWORD = None  # Set your MPD password if needed
MPD_PASSWORD = os.getenv("MPD_PASSWORD")

# Initialize Redis connection
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# ================
#  STATUS HANDLING 
# ================
fetch_status_lock = threading.Lock()
fetch_status = {
	'current_step': None,
	'start_time': None,
	'lyric_count': 0,
	'done_time': None
}

MESSAGES = {
	'start': "Starting lyric search...",
	'local': "Checking local files",
	'synced': "Searching online sources",
	'lrc_lib': "Checking LRCLIB database",
	'instrumental': "Instrumental track detected",
	'time_out': "In time-out log",
	'failed': "No lyrics found",
	'mpd':"scanning for MPD",
	'done': "Loaded",
	'clear': "",
}

TERMINAL_STATES = {'done', 'instrumental', 'time_out', 'failed', 'mpd', 'clear'}  # Ensure this is defined

def update_fetch_status(step, lyrics_found=0):
	"""Update the fetch status with the current progress"""
	with fetch_status_lock:
		fetch_status.update({
			'current_step': step,
			'lyric_count': lyrics_found,
			'start_time': time.time() if step == 'start' else fetch_status['start_time'],
			'done_time': time.time() if step in TERMINAL_STATES else None  # Modified line
		})

def get_current_status():
	"""Return a formatted status message"""
	with fetch_status_lock:
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
			end_time = fetch_status['done_time'] or time.time()  # Modified line
			elapsed = end_time - fetch_status['start_time']      # Modified line
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
	log_path = os.path.join(log_dir, DEBUG_LOG)
	
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

def log_debug(message):
	"""Conditionally log debug messages to file"""
	if not ENABLE_DEBUG_LOGGING:
		return

	# Create logs directory if missing
	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(log_dir, exist_ok=True)
	
	# Format log entry with timestamp
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | {message}\n"
	
	try:
		# Append to debug log
		with open(os.path.join(log_dir, DEBUG_LOG), 'a', encoding='utf-8') as f:
			f.write(log_entry)
		clean_debug_log()
	except Exception as e:
		pass  # Silently fail if logging fails

def clean_old_timeouts():
	"""Remove timeout entries older than retention period"""
	log_dir = os.path.join(os.getcwd(), "logs")
	log_path = os.path.join(log_dir, LYRICS_TIMEOUT_LOG)
	
	if not os.path.exists(log_path):
		return

	# Calculate cutoff date
	cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
	new_lines = []

	try:
		# Filter valid entries
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				line = line.strip()
				if not line:
					continue
				
				# Parse timestamp from log entry
				parts = line.split(' | ', 1)
				if len(parts) < 1:
					continue
				
				try:
					entry_time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
					if entry_time >= cutoff:
						new_lines.append(line + '\n')
				except ValueError:
					continue

		# Write back filtered entries
		with open(log_path, 'w', encoding='utf-8') as f:
			f.writelines(new_lines)
			
	except Exception as e:
		log_debug(f"Error cleaning timeout log: {e}")

def log_timeout(artist, title):
	"""Record failed lyric lookup with duplicate prevention"""
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
	
	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(log_dir, exist_ok=True)
	log_path = os.path.join(log_dir, LYRICS_TIMEOUT_LOG)

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

def parse_lrc_tags(lyrics):
	"""Extract metadata tags from LRC lyrics"""
	tags = {}
	for line in lyrics.split('\n'):
		match = re.match(r'^\[(ti|ar|al):(.+)\]$', line, re.IGNORECASE)
		if match:
			key = match.group(1).lower()
			value = match.group(2).strip()
			tags[key] = value
	return tags

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
		#update_fetch_status('clear')
		log_debug(f"MPD connection error: {str(e)}")
	except base.CommandError as e:
		#update_fetch_status('clear')
		log_debug(f"MPD command error: {str(e)}")
	except Exception as e:
		#update_fetch_status('clear')
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
		#update_fetch_status('clear')
		log_debug(f"MPD connection error: {str(e)}")
	except Exception as e:
		#update_fetch_status('clear')
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


		# if use_manual_offset:
			# start_screen_line = max(0, min(manual_offset, max_start))
		# else:
			# indices = [i for i, (orig, _) in enumerate(wrapped_lines) if orig == current_idx]
			# if indices:
				# center = (indices[0] + indices[-1]) // 2
				# ideal_start = center - (available_lines // 2)
				# start_screen_line = max(0, min(ideal_start, max_start))
			# else:
				# ideal_start = current_idx - (available_lines // 2)
				# start_screen_line = max(0, min(ideal_start, max_start))

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
				stdscr.addstr(y, x, status_msg) #curses.A_REVERSE | curses.A_BOLD)
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
executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
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

def main(stdscr):
	global future_lyrics
	# Curses setup
	curses.start_color()
	curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Active line
	curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)   # Inactive
	curses.curs_set(0)
	stdscr.timeout(200)

	# State variables
	current_audio_file, current_artist, current_title = None, None, None
	lyrics, errors = [], []
	is_txt_format, is_a2_format = False, False
	manual_offset, last_line_index = 0, -1
	last_input_time = None
	prev_window_size = stdscr.getmaxyx()
	manual_timeout_handled = True

	# Playback tracking
	last_player_position = 0.0
	last_position_time = time.time()
	estimated_position = 0.0
	current_duration = 0.0
	time_adjust = 0.0
	playback_paused = False
	last_status = None
	mpd_client = None  # Persistent MPD connection

	# Time synchronization threshold (0.1 seconds)
	SYNC_THRESHOLD = 0.1

	while True:
		try:
			current_time = time.time()
			needs_redraw = False
			time_since_input = current_time - (last_input_time or 0)

			# Get player status (CMUS or MPD)
			player_type, (audio_file, raw_position, artist, title, duration, status) = get_player_info()

			# # stopped state handling
			# if status == "stopped" and (current_audio_file is not None or lyrics):
				# current_audio_file = None
				# lyrics, errors = [], []
				# is_txt_format = is_a2_format = False
				# manual_offset = 0
				# needs_redraw = True
				# stdscr.clear()

			# status normalization in naming
			status_map = {
				'play': 'playing',
				'pause': 'paused',
				'stop': 'stopped'
			}
			status = status_map.get(status, status)

			# Unified position handling
			position = float(raw_position) if raw_position is not None else 0.0
			duration = float(duration) if duration is not None else 0.0
			now = time.time()

			# position tracking with threshold
			if abs(position - last_player_position) > SYNC_THRESHOLD:
				last_player_position = position
				last_position_time = now

			# time estimation
			if status == "playing":
				elapsed = now - last_position_time
				estimated_position = position + elapsed
				estimated_position = max(0.0, min(estimated_position, duration))
				last_position_time = now  # Update only if playing
				playback_paused = False
			elif status == "paused":
				estimated_position = position  # Freeze estimated position
				last_position_time = now  # Keep reference but don't advance
				playback_paused = True
			else:
				estimated_position = position
				playback_paused = False


			# track change detection
			if audio_file != current_audio_file:
				update_fetch_status('start')
				current_audio_file = audio_file
				current_artist = artist
				current_title = title
				last_player_position = position
				last_position_time = now
				estimated_position = position
				current_duration = duration
				needs_redraw = True
				stdscr.clear()

				# Maintain lyric loading functionality
				lyrics, errors = [], []
				is_txt_format, is_a2_format = False, False
				if audio_file:
					directory = os.path.dirname(audio_file) if player_type == 'cmus' else ""
					future_lyrics = executor.submit(
						fetch_lyrics_async,
						audio_file,
						directory,
						artist or "UnknownArtist",
						title or os.path.splitext(os.path.basename(audio_file))[0],
						duration
					)
			#else:
				#update_fetch_status('clear')

			# status message handling 
			current_status = get_current_status()
			if current_status != last_status:
				needs_redraw = True
				last_status = current_status

			# async lyric fetching
			is_fetching = future_lyrics is not None and not future_lyrics.done()

			# manual scroll functionality
			if last_input_time is not None:
				if time_since_input >= 2 and not manual_timeout_handled:
					needs_redraw = True
					manual_timeout_handled = True
				elif time_since_input < 2:
					manual_timeout_handled = False

			manual_scroll_active = last_input_time and (time_since_input < 2)

			# window resize handling
			current_window_size = stdscr.getmaxyx()
			if current_window_size != prev_window_size:
				old_height, _ = prev_window_size
				new_height, _ = current_window_size
				if old_height > 0 and new_height > 0:
					manual_offset = max(0, int(manual_offset * (new_height / old_height)))
				prev_window_size = current_window_size
				needs_redraw = True

			# lyric processing
			if future_lyrics and future_lyrics.done():
				try:
					result = future_lyrics.result(timeout=0.1)
					(new_lyrics, new_errors), new_txt, new_a2 = result
					lyrics = new_lyrics
					errors = new_errors
					is_txt_format = new_txt
					is_a2_format = new_a2
					needs_redraw = True
				except Exception as e:
					log_debug(f"Lyric fetch failed: {e}")
					lyrics, errors = [], []
					is_txt_format, is_a2_format = False, False
					needs_redraw = True
				finally:
					future_lyrics = None

			# # Keep stopped state cleanup
			# if status != last_status and status == "stopped":
				# current_audio_file = None
				# lyrics, errors = [], []
				# is_txt_format = is_a2_format = False
				# manual_offset = 0
				# needs_redraw = True
				# stdscr.clear()

			# ======================
			#  POSITION CALCULATION
			# ======================
			continuous_position = max(0.0, estimated_position + time_adjust)
			continuous_position = min(continuous_position, current_duration)

			# Create direct timestamp-to-index mapping
			timestamps = []
			valid_indices = []
			for idx, (t, _) in enumerate(lyrics):
				if t is not None:
					timestamps.append(t)
					valid_indices.append(idx)

			# Find the closest timestamp using binary search
			bisect_idx = bisect.bisect_right(timestamps, continuous_position)
			current_idx = valid_indices[bisect_idx - 1] if bisect_idx > 0 else -1

			# Handle edge case for first line
			if current_idx == -1 and len(lyrics) > 0:
				current_idx = 0 if continuous_position >= lyrics[0][0] else -1

			# Immediately highlight current position
			adjusted_position = lyrics[current_idx][0] if 0 <= current_idx < len(lyrics) else continuous_position

			# Force sync on any position change detection
			if abs(position - last_player_position) > SYNC_THRESHOLD:
				bisect_idx = bisect.bisect_right(timestamps, position)
				current_idx = valid_indices[bisect_idx - 1] if bisect_idx > 0 else -1
				last_player_position = position

			
			# input handling
			key = stdscr.getch()
			if key != -1:
				cont, manual_offset, last_input_time, needs_redraw_input, time_adjust = handle_scroll_input(
					key, manual_offset, last_input_time, needs_redraw, time_adjust)
				manual_timeout_handled = False
				needs_redraw |= needs_redraw_input
				if not cont:
					break

			# display update logic
			if needs_redraw or current_idx != last_line_index:
				manual_offset = update_display(
					stdscr, lyrics, errors, adjusted_position, audio_file, manual_offset,
					is_txt_format, is_a2_format, current_idx, manual_scroll_active,
					time_adjust=time_adjust, is_fetching=is_fetching)
				last_line_index = current_idx

		except Exception as e:
			log_debug(f"Main loop error: {str(e)}")
			continue

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(main)
		except KeyboardInterrupt:
			break
		except Exception as e:
			log_debug(f"Fatal error: {str(e)}")
			continue
			

