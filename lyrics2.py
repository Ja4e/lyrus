import curses
import subprocess
import re
import os
import bisect
import time
import textwrap
import requests
import urllib.parse
import syncedlyrics 
import multiprocessing

def sanitize_filename(name):
	"""Replace special characters with underscores to avoid filesystem issues."""
	return re.sub(r'[<>:"/\\|?*]', '_', name)

def sanitize_string(s):
	"""Normalize strings for comparison by removing non-alphanumeric chars and lowercasing"""
	return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

# def fetch_lyrics_lrclib(artist_name, track_name, duration=None):
	# """
	# Fetch lyrics using LRCLIB API by artist name and track name.
	# Returns a tuple (lyrics_content, is_synced) or (None, None) on error.
	# """
	# base_url = "https://lrclib.net/api/get"
	# params = {
		# 'artist_name': artist_name,
		# 'track_name': track_name,
	# }
	# if duration is not None:
		# params['duration'] = duration
	# try:
		# response = requests.get(base_url, params=params)
		# if response.status_code == 200:
			# data = response.json()
			# if data.get('instrumental', False):
				# return None, None  # Instrumental track
			# synced_lyrics = data.get('syncedLyrics', '')
			# plain_lyrics = data.get('plainLyrics', '')
			# if synced_lyrics.strip():
				# return synced_lyrics, True
			# elif plain_lyrics.strip():
				# return plain_lyrics, False
			# else:
				# return None, None  # No lyrics available
		# elif response.status_code == 404:
			# print("Lyrics not found on LRCLIB.")
			# return None, None
		# else:
			# print(f"Error fetching lyrics: HTTP {response.status_code}")
			# return None, None
	# except Exception as e:
		# print(f"Error fetching lyrics from LRCLIB: {e}")
		# return None, None

# def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None):
	# """
	# Fetch lyrics using the syncedlyrics library, prioritizing Enhanced LRC.
	# Returns a tuple (lyrics_content, is_synced) or (None, None) on error.
	# """
	# search_term = f"{track_name} {artist_name}".strip()
	# if not search_term:
		# return None, None
	# try:
		# # Attempt to fetch Enhanced LRC first
		# lyrics = syncedlyrics.search(search_term, enhanced=True)
		# if lyrics:
			# is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in lyrics.split('\n'))
			# is_synced = is_enhanced or any(re.search(r'\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
			# return lyrics, is_synced
		# # Fallback to regular synced or plain
		# lyrics = syncedlyrics.search(search_term)
		# if lyrics:
			# is_synced = any(re.search(r'\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
			# return lyrics, is_synced
		# return None, None
	# except Exception as e:
		# print(f"Error fetching lyrics via syncedlyrics: {e}")
		# return None, None
		

# def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None):
	# """
	# Fetch lyrics using the syncedlyrics library.
	# Returns a tuple (lyrics_content, is_synced) or (None, None) on error.
	# """
	# search_term = f"{track_name} {artist_name}".strip()
	# if not search_term:
		# return None, None
	# try:
		# # Attempt to fetch lyrics using syncedlyrics, preferring synced but allowing plain
		# lyrics = syncedlyrics.search(search_term)
		# if not lyrics:
			# return None, None
		# # Determine if the lyrics are synced by checking for timestamp lines
		# is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
		# return lyrics, is_synced
	# except Exception as e:
		# print(f"Error fetching lyrics via syncedlyrics: {e}")
		# return None, None

def parse_lrc_tags(lyrics):
	"""Extract LRC metadata tags from lyrics content"""
	tags = {}
	for line in lyrics.split('\n'):
		match = re.match(r'^\[(ti|ar|al):(.+)\]$', line, re.IGNORECASE)
		if match:
			key = match.group(1).lower()
			value = match.group(2).strip()
			tags[key] = value
	return tags


def validate_lyrics(content, artist, title):
	"""More lenient validation that allows approximate matches"""
	# Always allow files with timestamps through
	if re.search(r'\[\d+:\d+\.\d+\]', content):
		return True
		
	# Check for instrumental marker
	if re.search(r'\b(instrumental)\b', content, re.IGNORECASE):
		return True

	# Normalize without being too aggressive
	def normalize(s):
		return re.sub(r'[^\w]', '', str(s)).lower().replace(' ', '')[:15]

	# Check for partial matches
	norm_title = normalize(title)[:15]
	norm_artist = normalize(artist)[:15] if artist else ''
	norm_content = normalize(content)

	title_match = norm_title in norm_content if norm_title else True
	artist_match = norm_artist in norm_content if norm_artist else True

	return title_match or artist_match

# def bar():
	# try:
		# for i in range(100):
			# time.sleep(1)
	# except KeyboardInterrupt:
		# exit()

def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None, timeout=15):
	"""Fetch lyrics with strict validation against track metadata, with timeout handling"""
	def worker(queue, search_term):
		"""Worker function to fetch lyrics"""
		try:
			lyrics = syncedlyrics.search(search_term)
			# lyrics = syncedlyrics.search(search_term, enhanced=True) CURRENTLY A2 IS NOT WORKING PROPERLY
			queue.put(lyrics)  # Store result in queue
		except Exception as e:
			queue.put(None)

	search_term = f"{track_name} {artist_name}".strip()
	if not search_term:
		return None, None
	
	queue = multiprocessing.Queue()
	process = multiprocessing.Process(target=worker, args=(queue, search_term))
	process.start()
	process.join(timeout)  # Wait for the process with a timeout

	if process.is_alive():
		process.terminate()  # Kill the process if it exceeds timeout
		process.join()  # Ensure it's cleaned up
		print("Lyrics fetch timed out")
		return None, None

	lyrics = queue.get() if not queue.empty() else None
	if not lyrics:
		return None, None
	
	if not validate_lyrics(lyrics, artist_name, track_name):
		print("Lyrics validation failed - metadata mismatch")
		return None, None

	is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
	return lyrics, is_synced

# def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None):
	# """Fetch lyrics with strict validation against track metadata"""
	# search_term = f"{track_name} {artist_name}".strip()
	# if not search_term:
		# return None, None
	
	# try:
		# # p = multiprocessing.Process(target=bar)
		# # p.start()
		# #lyrics = syncedlyrics.search(search_term, enhanced=True)
		# lyrics = syncedlyrics.search(search_term)
		# if not lyrics:
			# return None, None
			
		# if not validate_lyrics(lyrics, artist_name, track_name):
			# print("Lyrics validation failed - metadata mismatch")
			# return None, None
			
		# is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) 
						  # for line in lyrics.split('\n'))
		# # p.join(15)
		# # if p.is_alive():
			# # print("Failed to fetch in time... killing process")
			# # p.terminate()
			# # p.kill()
			# # p.join()
			# # return None, None
		# return lyrics, is_synced
	# except Exception as e:
		# print(f"Error fetching lyrics: {e}")
		# return None, None
	
def save_lyrics(lyrics, track_name, artist_name, extension):
	"""Save lyrics to a sanitized filename with appropriate extension."""
	folder = os.path.join(os.getcwd(), "synced_lyrics")
	os.makedirs(folder, exist_ok=True)
	sanitized_track = sanitize_filename(track_name)
	sanitized_artist = sanitize_filename(artist_name)
	
	filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
	file_path = os.path.join("synced_lyrics", filename)
	
	with open(file_path, "w", encoding="utf-8") as f:
		f.write(lyrics)
	
	return file_path

def get_cmus_info():
	try:
		result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		if result.returncode != 0:
			return None, 0, None, None, 0
		output = result.stdout.decode('utf-8')
	except Exception:
		return None, 0, None, None, 0

	track_file = None
	position = 0-1	
	artist = None
	title = None
	duration = 0

	for line in output.split('\n'):
		line = line.strip()
		if line.startswith('file '):
			track_file = line[5:].strip()
		elif line.startswith('tag artist '):
			artist = line[11:].strip()
		elif line.startswith('tag title '):
			title = line[10:].strip()
		elif line.startswith('duration '):
			parts = line.split()
			if len(parts) >= 2:
				duration = int(parts[1])
		elif line.startswith('position '):
			parts = line.split()
			if len(parts) >= 2:
				position = int(parts[1])

	return track_file, position, artist, title, duration

# def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	# base_name, _ = os.path.splitext(os.path.basename(audio_file))

	# a2_file = os.path.join(directory, f"{base_name}.a2")
	# lrc_file = os.path.join(directory, f"{base_name}.lrc")
	# txt_file = os.path.join(directory, f"{base_name}.txt")

	# # Check local files
	# if os.path.exists(a2_file):
		# print("Using local .a2 file")
		# return a2_file
	# elif os.path.exists(lrc_file):
		# print("Using local .lrc file")
		# return lrc_file
	# elif os.path.exists(txt_file):
		# print("Using local .txt file")
		# return txt_file
	
	# sanitized_track = sanitize_filename(track_name)
	# sanitized_artist = sanitize_filename(artist_name)

	# # Construct expected filenames
	# possible_filenames = [
		# f"{sanitized_track}.a2",
		# f"{sanitized_track}.lrc",
		# f"{sanitized_track}.txt",
		# f"{sanitized_track}_{sanitized_artist}.a2",
		# f"{sanitized_track}_{sanitized_artist}.lrc",
		# f"{sanitized_track}_{sanitized_artist}.txt"
	# ]

	# synced_dir = os.path.join(os.getcwd(), "synced_lyrics")

	# # Search in both directories
	# for dir_path in [directory, synced_dir]:
		# for filename in possible_filenames:
			# file_path = os.path.join(dir_path, filename)
			# if os.path.exists(file_path):
				# print(f"[DEBUG] Found lyrics: {file_path}")
				# return file_path

	# print("[DEBUG] No local nor cached file found, fetching from snycedlyrics...")
	
	# # # Fetch from LRCLIB only if no local file exists
	# # fetched_lyrics, is_synced = fetch_lyrics_lrclib(artist_name, track_name, duration)
	# # if fetched_lyrics:
		# # extension = 'lrc' if is_synced else 'txt'
		# # return save_lyrics(fetched_lyrics, track_name, artist_name, extension)

	# # print("[DEBUG] LRCLIB failed, trying syncedlyrics...")

	# # Fallback to syncedlyrics
	# fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	# if fetched_lyrics:
		# is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
		# extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
		# return save_lyrics(fetched_lyrics, track_name, artist_name, extension)

	# print("[ERROR] No lyrics found from any source.")
	# return None
	
# def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	# base_name, _ = os.path.splitext(os.path.basename(audio_file))

	# a2_file = os.path.join(directory, f"{base_name}.a2")
	# lrc_file = os.path.join(directory, f"{base_name}.lrc")
	# txt_file = os.path.join(directory, f"{base_name}.txt")

	# # Check local files
	# if os.path.exists(a2_file):
		# print("Using local .a2 file")
		# return a2_file
	# elif os.path.exists(lrc_file):
		# print("Using local .lrc file")
		# return lrc_file
	# elif os.path.exists(txt_file):
		# print("Using local .txt file")
		# return txt_file

	# sanitized_track = sanitize_filename(track_name)
	# sanitized_artist = sanitize_filename(artist_name)

	# # Construct expected filenames
	# possible_filenames = [
		# f"{sanitized_track}.a2",
		# f"{sanitized_track}.lrc",
		# f"{sanitized_track}.txt",
		# f"{sanitized_track}_{sanitized_artist}.a2",
		# f"{sanitized_track}_{sanitized_artist}.lrc",
		# f"{sanitized_track}_{sanitized_artist}.txt"
	# ]

	# synced_dir = os.path.join(os.getcwd(), "synced_lyrics")

	# # Search in both directories
	# for dir_path in [directory, synced_dir]:
		# for filename in possible_filenames:
			# file_path = os.path.join(dir_path, filename)
			# if os.path.exists(file_path):
				# print(f"[DEBUG] Found lyrics: {file_path}")
				# return file_path

	# print("[DEBUG] No local nor cached file found, fetching from syncedlyrics...")
	
	# # Fallback to syncedlyrics
	# fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	# if fetched_lyrics:
		# is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
		# extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')

		# # Save the lyrics and ensure they're valid
		# saved_lyrics_path = save_lyrics(fetched_lyrics, track_name, artist_name, extension)

		# # Return the saved path, so it can be loaded
		# return saved_lyrics_path

	# print("[ERROR] No lyrics found from any source.")
	# return None
	
def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	base_name, _ = os.path.splitext(os.path.basename(audio_file))

	# Check local files first (original structure)
	a2_file = os.path.join(directory, f"{base_name}.a2")
	lrc_file = os.path.join(directory, f"{base_name}.lrc")
	txt_file = os.path.join(directory, f"{base_name}.txt")

	local_files = [
		(a2_file, 'a2'),
		(lrc_file, 'lrc'), 
		(txt_file, 'txt')
	]

	# Modified validation check with fallback
	for file_path, ext in local_files:
		if os.path.exists(file_path):
			try:
				with open(file_path, 'r', encoding='utf-8') as f:
					content = f.read()
				
				# Give priority to unvalidated local files over fetched ones
				if validate_lyrics(content, artist_name, track_name):
					print(f"Using validated local .{ext} file")
					return file_path
				else:
					# Still use local file but warn about validation
					print(f"Using unvalidated local .{ext} file (fallback)")
					return file_path
					
			except Exception as e:
				print(f"Error reading {file_path}: {e}")

	# Check if metadata indicates instrumental
	is_instrumental_metadata = (
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

	# Search in both directories with validation
	for dir_path in [directory, synced_dir]:
		for filename in possible_filenames:
			file_path = os.path.join(dir_path, filename)
			if os.path.exists(file_path):
				try:
					with open(file_path, 'r', encoding='utf-8') as f:
						content = f.read()
					if validate_lyrics(content, artist_name, track_name):
						print(f"Using validated file: {file_path}")
						return file_path
					else:
						print(f"Skipping invalid file: {file_path}")
				except Exception as e:
					print(f"Error reading {file_path}: {e}")

	# Instrumental handling (keep your existing logic)
	if is_instrumental_metadata:
		print("[INFO] Instrumental track detected via metadata")
		return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

	print("[DEBUG] Fetching from syncedlyrics...")
	
	# Modified fetched lyrics handling
	fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	
	if fetched_lyrics:
		# Add tolerant validation
		if not validate_lyrics(fetched_lyrics, artist_name, track_name):
			# Instead of discarding, add warning and use anyway
			print("Validation warning - using lyrics with caution")
			fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

		# Keep your existing saving logic
		is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
		extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	print("[ERROR] No lyrics found")
	return None

# def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	# base_name, _ = os.path.splitext(os.path.basename(audio_file))

	# a2_file = os.path.join(directory, f"{base_name}.a2")
	# lrc_file = os.path.join(directory, f"{base_name}.lrc")
	# txt_file = os.path.join(directory, f"{base_name}.txt")

	# # Check local files first
	# local_files = {
		# 'a2': os.path.exists(a2_file),
		# 'lrc': os.path.exists(lrc_file),
		# 'txt': os.path.exists(txt_file)
	# }
	
	# if local_files['a2']:
		# print("Using local .a2 file")
		# return a2_file
	# elif local_files['lrc']:
		# print("Using local .lrc file")
		# return lrc_file
	# elif local_files['txt']:
		# print("Using local .txt file")
		# return txt_file

	# # Check if metadata indicates instrumental
	# is_instrumental_metadata = (
		# "instrumental" in track_name.lower() or 
		# (artist_name and "instrumental" in artist_name.lower())
	# )
	
	# sanitized_track = sanitize_filename(track_name)
	# sanitized_artist = sanitize_filename(artist_name)
	# possible_filenames = [
		# f"{sanitized_track}.a2",
		# f"{sanitized_track}.lrc",
		# f"{sanitized_track}.txt",
		# f"{sanitized_track}_{sanitized_artist}.a2",
		# f"{sanitized_track}_{sanitized_artist}.lrc",
		# f"{sanitized_track}_{sanitized_artist}.txt"
	# ]

	# synced_dir = os.path.join(os.getcwd(), "synced_lyrics")
	
	# # Search in both directories
	# for dir_path in [directory, synced_dir]:
		# for filename in possible_filenames:
			# file_path = os.path.join(dir_path, filename)
			# if os.path.exists(file_path):
				# print(f"[DEBUG] Found lyrics: {file_path}")
				# return file_path

	# # If metadata indicates instrumental, save and return instrumental marker
	# if is_instrumental_metadata:
		# print("[INFO] Instrumental track detected via metadata")
		# return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

	# print("[DEBUG] No local nor cached file found, fetching from syncedlyrics...")
	
	# # Fetch lyrics
	# fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	
	# if fetched_lyrics:
		# # Check if lyrics contain instrumental marker
		# if re.search(r'\[Instrumental\]', fetched_lyrics, re.IGNORECASE):
			# print("[INFO] Instrumental track detected via lyrics content")
			# return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')
		# else:
			# # Determine extension and save normally
			# is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
			# extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
			# return save_lyrics(fetched_lyrics, track_name, artist_name, extension)

	# print("[ERROR] No lyrics found from any source.")
	# return None

def parse_time_to_seconds(time_str):
	minutes, seconds = time_str.split(':')
	seconds, milliseconds = seconds.split('.')
	return max(0, int(minutes) * 60 + int(seconds) + float(f"0.{milliseconds}"))

def load_lyrics(file_path):
	lyrics = []
	errors = []
	
	try:
		with open(file_path, 'r', encoding="utf-8") as f:
			lines = f.readlines()
	except Exception as e:
		errors.append(f"Error opening file {file_path}: {str(e)}").strip()
		return lyrics, errors


	if file_path.endswith('.a2'):
		current_line_time = None
		line_pattern = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
		word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')

		for line in lines:
			line = line.strip()
			if not line:
				continue

			# Parse line with aggressive timestamp removal
			clean_line = re.sub(r'\[.*?\]|<.*?>', '', line).strip()
			if not clean_line:
				continue

			# Extract and store word timestamps
			line_match = line_pattern.match(line)
			if line_match:
				current_line_time = parse_time_to_seconds(line_match.group(1))
				lyrics.append((current_line_time, None))  # Line start
				
				content = line_match.group(2)
				words = word_pattern.findall(content)
				
				for start_str, text, end_str in words:
					start = parse_time_to_seconds(start_str)
					end = parse_time_to_seconds(end_str)
					clean_text = re.sub(r'<.*?>', '', text).strip()
					if clean_text:
						lyrics.append((start, (clean_text, end)))
				
				# Add remaining text with line timestamp
				remaining_text = re.sub(word_pattern, '', content).strip()
				if remaining_text:
					lyrics.append((current_line_time, (remaining_text, current_line_time)))

				lyrics.append((current_line_time, None))  # Line end


	# Check for TXT format or other formats
	elif file_path.endswith('.txt'):
		for line in lines:
			raw_line = line.rstrip('\n')
			if not raw_line.strip():
				lyrics.append((None, ""))
				continue
			lyrics.append((None, " " + raw_line))

	else:
		for line in lines:
			raw_line = line.rstrip('\n')
			if not raw_line.strip():
				lyrics.append((None, ""))
				continue
			# Match line timestamp (square brackets) and Enhanced word timestamps (angle brackets)
			line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
			if line_match:
				line_time = parse_time_to_seconds(line_match.group(1))
				lyric_content = line_match.group(2)
				# Split into word timestamps if Enhanced LRC
				words = re.findall(r'<(\d+:\d+\.\d+)>(.*?)(?=(<\d+:\d+\.\d+>)|$)', lyric_content)
				if words:
					for word_time_str, word_text, _ in words:
						try:
							word_time = parse_time_to_seconds(word_time_str)
							lyrics.append((word_time, word_text.strip()))
						except:
							errors.append(f"Invalid word timestamp: {word_time_str}").strip()
					# Also add the line time for fallback
					lyrics.append((line_time, lyric_content.strip()))
				else:
					lyrics.append((line_time, lyric_content.strip()))
			else:
				lyrics.append((None, raw_line))
				errors.append(raw_line)
	
	return lyrics, errors


def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset):
	height, width = stdscr.getmaxyx()
	start_screen_line = 0  # Default value
	if is_a2_format:
		a2_lines = []
		current_line = []
		for t, item in lyrics:
			if item is None:
				if current_line:
					a2_lines.append(current_line)
					current_line = []
			else:
				current_line.append((t, item))

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

		stdscr.clear()
		current_y = 1
		visible_lines = height - 2
		start_line = max(0, active_line_idx - visible_lines // 2)
		
		for line_idx in range(start_line, min(start_line + visible_lines, len(a2_lines))):
			if current_y >= height - 1:
				break

			line = a2_lines[line_idx]
			line_str = " ".join([text for _, (text, _) in line])
			x_pos = max(0, (width - len(line_str)) // 2)
			x_pos = min(x_pos, width - 1)
			
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

	else:
		available_lines = height - 3
		wrap_width = width - 2
		wrapped_lines = []
		
		for orig_idx, (_, lyric) in enumerate(lyrics):
			if lyric.strip():
				lines = textwrap.wrap(lyric, wrap_width, drop_whitespace=False)
				if lines:
					wrapped_lines.append((orig_idx, lines[0]))
					for line in lines[1:]:
						wrapped_lines.append((orig_idx, " " + line))
			else:
				wrapped_lines.append((orig_idx, ""))
		
		total_wrapped = len(wrapped_lines)
		max_start = max(0, total_wrapped - available_lines)

		if use_manual_offset:
			start_screen_line = max(0, min(manual_offset, max_start))
		else:
			indices = [i for i, (orig, _) in enumerate(wrapped_lines) if orig == current_idx]
			if indices:
				center = (indices[0] + indices[-1]) // 2
				ideal_start = center - (available_lines // 2)
				start_screen_line = max(0, min(ideal_start, max_start))
			else:
				ideal_start = current_idx - (available_lines // 2)
				start_screen_line = max(0, min(ideal_start, max_start))


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
		
		if current_idx is not None and current_idx < len(lyrics):
			status = f"Line {current_idx+1}/{len(lyrics)} "[:width-2]
			if height > 1:
				stdscr.addstr(height-1, 0, status, curses.A_BOLD)
		
		if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format:
			if height > 2:
				stdscr.addstr(height-2, 0, "End of lyrics ", curses.A_BOLD)
		
	stdscr.refresh()
	return start_screen_line


# def main(stdscr):
	# curses.start_color()
	# curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	# curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	# curses.curs_set(0)
	# stdscr.timeout(200)
	# current_audio_file = None
	# current_artist = None  # Track current artist
	# current_title = None   # Track current title
	# lyrics = []
	# errors = []
	# is_txt_format = False
	# is_a2_format = False
	# last_input_time = None
	# manual_offset = 0
	# last_redraw = 0
	# last_position = -1
	# last_line_index = -1
	# last_active_words = set()  # Track previously highlighted words
	
	# while True:
		# current_time = time.time()
		# needs_redraw = False

		# audio_file, position, artist, title, duration = get_cmus_info()
		
		# # **Force redraws only for A2 format**
		# if is_a2_format:
			# active_words = set()
			# a2_lines = []
			# current_line = []
			
			# # Group words by line
			# for t, item in lyrics:
				# if item is None:
					# if current_line:
						# a2_lines.append(current_line)
						# current_line = []
				# else:
					# current_line.append((t, item))
			
			# # Identify active words based on position
			# for line in a2_lines:
				# for word_idx, (start, (text, end)) in enumerate(line):
					# if start <= position < end:
						# active_words.add((text, word_idx))  # Track active words
			
			# # **Trigger redraw if active words changed**
			# if active_words != last_active_words:
				# needs_redraw = True
				# last_active_words = active_words

		# # **Trigger redraw if position changed (for A2)**
		# if is_a2_format and position != last_position:
			# needs_redraw = True
		
		# if (audio_file != current_audio_file or 
			# artist != current_artist or 
			# title != current_title):
				# # Update current tracking variables
			# current_audio_file = audio_file
			# current_artist = artist
			# current_title = title
			# while True:
				# current_time = time.time()
				# needs_redraw = True

				# audio_file, position, artist, title, duration = get_cmus_info()
				# # Update current tracking variables
				# current_audio_file = audio_file
				# current_artist = artist
				# current_title = title

				# # Reset lyric state
				# last_line_index = -1
				# manual_offset = 0
				# last_input_time = None
				# lyrics = []
				# errors = []

				# # **Force redraws only for A2 format**
				# if is_a2_format:
					# active_words = set()
					# a2_lines = []
					# current_line = []
					
					# # Group words by line
					# for t, item in lyrics:
						# if item is None:
							# if current_line:
								# a2_lines.append(current_line)
								# current_line = []
						# else:
							# current_line.append((t, item))
					
					# # Identify active words based on position
					# for line in a2_lines:
						# for word_idx, (start, (text, end)) in enumerate(line):
							# if start <= position < end:
								# active_words.add((text, word_idx))  # Track active words
					
					# # **Trigger redraw if active words changed**
					# if active_words != last_active_words:
						# needs_redraw = True
						# last_active_words = active_words

				# # **Trigger redraw if position changed (for A2)**
				# if is_a2_format and position != last_position:
					# needs_redraw = True

				# if audio_file:
					# directory = os.path.dirname(audio_file)
					# artist_name = current_artist if current_artist else "UnknownArtist"
					# track_name = current_title if current_title else os.path.splitext(os.path.basename(audio_file))[0]
					# lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)
					# if lyrics_file:
						# is_txt_format = lyrics_file.endswith('.txt')
						# is_a2_format = lyrics_file.endswith('.a2') if lyrics_file else False
						# lyrics, errors = load_lyrics(lyrics_file)

				# # Calculate current_idx based on position and lyrics
				# current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
				# manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)

				# # Call display_lyrics based on the file format
				# if not is_txt_format:
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=manual_scroll_active
					# )
					# manual_offset = new_manual_offset
				# else:
					# # For txt files, allow manual scrolling but prevent auto-scroll based on position
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=True  # Allow manual scrolling
					# )
					# manual_offset = new_manual_offset

				# last_position = position
				# last_redraw = current_time
						# # Prevent auto-scroll for txt files, but allow manual scroll
				# height, width = stdscr.getmaxyx()
				# available_lines = height - 3
				# # If manual scroll is active, override the last_line_index to -1 to force redraw
				# if manual_scroll_active:
					# last_line_index = -1
				
				# if position != last_position or needs_redraw:
					# last_position = position
					# last_redraw = current_time
				
				# current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
				# manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)
				# if audio_file and (needs_redraw or (current_time - last_redraw >= 0.1) or position != last_position):
					# if current_idx != last_line_index:  # Only redraw if the line has changed (or manual scroll)
						# if not is_txt_format:
							# new_manual_offset = display_lyrics(
								# stdscr,
								# lyrics,
								# errors,
								# position,
								# os.path.basename(audio_file),
								# manual_offset,
								# is_txt_format,
								# is_a2_format,
								# current_idx,
								# use_manual_offset=manual_scroll_active
							# )
							# manual_offset = new_manual_offset
						# else:
							# new_manual_offset = display_lyrics(
								# stdscr,
								# lyrics,
								# errors,
								# position,
								# os.path.basename(audio_file),
								# manual_offset,
								# is_txt_format,
								# is_a2_format,
								# current_idx,
								# use_manual_offset=True  # Always allow manual scrolling for .txt files
							# )
							# manual_offset = new_manual_offset

						# last_line_index = current_idx  # Update last displayed line index
					# last_position = position
					# last_redraw = current_time

				# # Force a refresh if manual input has gone inactive for 2 seconds
				# if last_input_time and (current_time - last_input_time >= 2):
					# last_line_index = -1  # Reset the last displayed line index
					# needs_redraw = True
					# last_input_time = None  # Reset the last input time after forcing a refresh
				
				# key = stdscr.getch()
				# if key == ord('q'):
					# break
				# elif key == curses.KEY_UP:
					# manual_offset = max(0, manual_offset - 1)
					# last_input_time = current_time
					# needs_redraw = True
				# elif key == curses.KEY_DOWN:
					# manual_offset += 1
					# last_input_time = current_time
					# needs_redraw = True
				# elif key == curses.KEY_RESIZE:
					# last_line_index = -1  
					# needs_redraw = True
					# last_redraw = current_time

				# # Redraw lyrics if necessary
				# #if needs_redraw or audio_file or position != last_position:
				# if position != last_position or (lyrics and (current_time - last_redraw >= 0.1)) or needs_redraw:
					# if current_idx != last_line_index:  # Only redraw if the line has changed or manual scroll
						# if not is_txt_format:
							# new_manual_offset = display_lyrics(
								# stdscr,
								# lyrics,
								# errors,
								# position,
								# os.path.basename(audio_file),
								# manual_offset,
								# is_txt_format,
								# is_a2_format,
								# current_idx,
								# use_manual_offset=manual_scroll_active
							# )
							# manual_offset = new_manual_offset
						# else:
							# new_manual_offset = display_lyrics(
								# stdscr,
								# lyrics,
								# errors,
								# position,
								# os.path.basename(audio_file),
								# manual_offset,
								# is_txt_format,
								# is_a2_format,
								# current_idx,
								# use_manual_offset=True  # Always allow manual scrolling for .txt files
							# )
							# manual_offset = new_manual_offset

						# last_line_index = current_idx  # Update last displayed line index
					# last_redraw = current_time
				# if position != last_position:
					# break

		# # Prevent auto-scroll for txt files, but allow manual scroll
		# height, width = stdscr.getmaxyx()
		# available_lines = height - 3
		# # Recalculate current_idx and manual_scroll_active each loop iteration
		# current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
		# manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)

		# # If manual scroll is active, override the last_line_index to -1 to force redraw
		# if manual_scroll_active:
			# last_line_index = -1
		
		# if audio_file and (needs_redraw or (current_time - last_redraw >= 0.1) or position != last_position):
			# if current_idx != last_line_index:  # Only redraw if the line has changed (or manual scroll)
				# if not is_txt_format:
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=manual_scroll_active
					# )
					# manual_offset = new_manual_offset
				# else:
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=True  # Always allow manual scrolling for .txt files
					# )
					# manual_offset = new_manual_offset

				# last_line_index = current_idx  # Update last displayed line index
			# last_position = position
			# last_redraw = current_time

		# # Force a refresh if manual input has gone inactive for 2 seconds
		# if last_input_time and (current_time - last_input_time >= 2):
			# last_line_index = -1  # Reset the last displayed line index
			# needs_redraw = True
			# last_input_time = None  # Reset the last input time after forcing a refresh
		
		# key = stdscr.getch()
		# if key == ord('q'):
			# break
		# elif key == curses.KEY_UP:
			# manual_offset = max(0, manual_offset - 1)
			# last_input_time = current_time
			# needs_redraw = True
		# elif key == curses.KEY_DOWN:
			# manual_offset += 1
			# last_input_time = current_time
			# needs_redraw = True
		# elif key == curses.KEY_RESIZE:
			# last_line_index = -1  
			# needs_redraw = True
			# last_redraw = current_time
		# if position != last_position or needs_redraw:
			# last_position = position
			# last_redraw = current_time
		
		# # Redraw lyrics if necessary
		# #if needs_redraw or audio_file or position != last_position:
		# if position != last_position or (lyrics and (current_time - last_redraw >= 0.1)) or needs_redraw:
			# if current_idx != last_line_index:  # Only redraw if the line has changed or manual scroll
				# if not is_txt_format:
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=manual_scroll_active
					# )
					# manual_offset = new_manual_offset
				# else:
					# new_manual_offset = display_lyrics(
						# stdscr,
						# lyrics,
						# errors,
						# position,
						# os.path.basename(audio_file),
						# manual_offset,
						# is_txt_format,
						# is_a2_format,
						# current_idx,
						# use_manual_offset=True  # Always allow manual scrolling for .txt files
					# )
					# manual_offset = new_manual_offset

				# last_line_index = current_idx  # Update last displayed line index
			# last_redraw = current_time

def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw):
	if key == ord('q'):
		return False, manual_offset, last_input_time, needs_redraw
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
	return True, manual_offset, last_input_time, needs_redraw

def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, is_txt_format, is_a2_format, current_idx, manual_scroll_active):
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset=True)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset=manual_scroll_active)


def main(stdscr):
	curses.start_color()
	curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	curses.curs_set(0)
	stdscr.timeout(1000)
	
	current_audio_file, current_artist, current_title = None, None, None
	lyrics, errors = [], []
	is_txt_format, is_a2_format = False, False
	last_input_time, manual_offset = None, 0
	last_redraw, last_position = 0, -1
	last_active_words, last_line_index = set(), -1
	
	while True:
		current_time = time.time()
		needs_redraw = False
		audio_file, position, artist, title, duration = get_cmus_info()

		# Redraw only if position change correlates with lyrics change (A2 format)
		if is_a2_format:
			active_words = set()
			a2_lines = []
			current_line = []

			# Group words by line
			for t, item in lyrics:
				if item is None:
					if current_line:
						a2_lines.append(current_line)
						current_line = []
				else:
					current_line.append((t, item))

			# Highlight active words based on position
			for line in a2_lines:
				for word_idx, (start, (text, end)) in enumerate(line):
					if start <= position < end:
						active_words.add((text, word_idx))

			# Only redraw if the active words or position has changed
			if active_words != last_active_words or position != last_position:
				needs_redraw = True
				last_active_words = active_words

		# Check if audio info has changed (force redraw)
		# Check if audio info has changed (force immediate redraw)
		if (audio_file != current_audio_file or artist != current_artist or title != current_title):
			current_audio_file, current_artist, current_title = audio_file, artist, title
			lyrics, errors = [], []
			last_line_index, manual_offset = -1, 0
			last_input_time = None
			needs_redraw = True  # Force immediate redraw

			# Load lyrics based on format
			directory = os.path.dirname(audio_file)
			artist_name = current_artist or "UnknownArtist"
			track_name = current_title or os.path.splitext(os.path.basename(audio_file))[0]
			lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)

			if lyrics_file:
				is_txt_format = lyrics_file.endswith('.txt')
				is_a2_format = lyrics_file.endswith('.a2')
				lyrics, errors = load_lyrics(lyrics_file)

			# Immediate refresh upon track change
			current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
			manual_scroll_active = False
			manual_offset = update_display(
				stdscr, lyrics, errors, position, audio_file, manual_offset,
				is_txt_format, is_a2_format, current_idx, manual_scroll_active
			)
			last_position = position
			last_redraw = time.time()


		# Check if the position has changed enough to affect the lyrics
		current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
		if current_idx != last_line_index:
			needs_redraw = True
			last_line_index = current_idx
		
		manual_scroll_active = last_input_time and (current_time - last_input_time < 2)
		
		# Update display with the current lyrics only if necessary
		if needs_redraw:
			new_manual_offset = update_display(
				stdscr, lyrics, errors, position, audio_file, manual_offset, 
				is_txt_format, is_a2_format, current_idx, manual_scroll_active
			)
			manual_offset = new_manual_offset
			last_position = position
			last_redraw = current_time

		# Handle key input for scrolling
		key = stdscr.getch()
		continue_running, manual_offset, last_input_time, needs_redraw = handle_scroll_input(
			key, manual_offset, last_input_time, needs_redraw
		)
		if not continue_running:
			break

		# Force a refresh if no input for 2 seconds
		if last_input_time and (current_time - last_input_time >= 2):
			last_line_index = -1
			needs_redraw = True
			last_input_time = None

		# Redraw lyrics if necessary due to position change or other conditions
		if needs_redraw:
			current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
			new_manual_offset = update_display(
				stdscr, lyrics, errors, position, audio_file, manual_offset,
				is_txt_format, is_a2_format, current_idx, manual_scroll_active
			)
			manual_offset = new_manual_offset

		# Handle window resize
		if key == curses.KEY_RESIZE:
			needs_redraw = True

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(main)
		except KeyboardInterrupt:
			break
			exit()
		except:
			continue
