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

LYRICS_TIMEOUT_LOG = "lyrics_timeouts.log"
LOG_RETENTION_DAYS = 10

def log_timeout(artist, title):
	"""Log timeout with automatic cleanup of old entries"""
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
	
	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(log_dir, exist_ok=True)
	
	log_path = os.path.join(log_dir, LYRICS_TIMEOUT_LOG)
	
	try:
		with open(log_path, 'a', encoding='utf-8') as f:
			f.write(log_entry)
		clean_old_timeouts()
		
	except Exception as e:
		print(f"Failed to write timeout log: {e}")
		
def clean_old_timeouts():
	"""Remove log entries older than LOG_RETENTION_DAYS days"""
	log_dir = os.path.join(os.getcwd(), "logs")
	log_path = os.path.join(log_dir, LYRICS_TIMEOUT_LOG)
	
	if not os.path.exists(log_path):
		return

	cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
	new_lines = []

	try:
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				line = line.strip()
				if not line:
					continue
				
				# Extract timestamp from log line format: "YYYY-MM-DD HH:MM:SS | Artist: ..."
				parts = line.split(' | ', 1)
				if len(parts) < 1:
					continue
				
				try:
					entry_time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
					if entry_time >= cutoff:
						new_lines.append(line + '\n')
				except ValueError:
					continue

		# Write filtered lines back to the file
		with open(log_path, 'w', encoding='utf-8') as f:
			f.writelines(new_lines)
			
	except Exception as e:
		print(f"Error cleaning log file: {e}")

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
	if re.search(r'\[\d+:\d+\.\d+\]', content):
		return True
		
	if re.search(r'\b(instrumental)\b', content, re.IGNORECASE):
		return True

	def normalize(s):
		return re.sub(r'[^\w]', '', str(s)).lower().replace(' ', '')[:15]

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
	process.join(timeout)

	if process.is_alive():
		process.terminate() 
		process.join()
		print("Lyrics fetch timed out")
		log_timeout(artist_name, track_name)  # logging
		return None, None

	lyrics = queue.get() if not queue.empty() else None
	if not lyrics:
		return None, None
	
	if not validate_lyrics(lyrics, artist_name, track_name):
		print("Lyrics validation failed - metadata mismatch")
		return None, None

	is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
	return lyrics, is_synced

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
			return None, 0, None, None, 0, "stopped"
		output = result.stdout.decode('utf-8')
	except Exception:
		return None, 0, None, None, 0, "stopped"

	track_file = None
	position = 0
	artist = None
	title = None
	duration = 0
	status = "stopped"

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
		elif line.startswith('status '): 
			status = line.split()[1]

	return track_file, position, artist, title, duration, status

	# # # Fetch from LRCLIB only if no local file exists
	# # fetched_lyrics, is_synced = fetch_lyrics_lrclib(artist_name, track_name, duration)
	# # if fetched_lyrics:
		# # extension = 'lrc' if is_synced else 'txt'
		# # return save_lyrics(fetched_lyrics, track_name, artist_name, extension)

	# # print("[DEBUG] LRCLIB failed, trying syncedlyrics...")

	
def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	base_name, _ = os.path.splitext(os.path.basename(audio_file))

	a2_file = os.path.join(directory, f"{base_name}.a2")
	lrc_file = os.path.join(directory, f"{base_name}.lrc")
	txt_file = os.path.join(directory, f"{base_name}.txt")

	local_files = [
		(a2_file, 'a2'),
		(lrc_file, 'lrc'), 
		(txt_file, 'txt')
	]

	for file_path, ext in local_files:
		if os.path.exists(file_path):
			try:
				with open(file_path, 'r', encoding='utf-8') as f:
					content = f.read()
				
				if validate_lyrics(content, artist_name, track_name):
					print(f"Using validated local .{ext} file")
					return file_path
				else:
					print(f"Using unvalidated local .{ext} file (fallback)")
					return file_path
					
			except Exception as e:
				print(f"Error reading {file_path}: {e}")

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

	if is_instrumental_metadata:
		print("[INFO] Instrumental track detected via metadata")
		return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

	print("[DEBUG] Fetching from syncedlyrics...")

	fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	
	if fetched_lyrics:
		if not validate_lyrics(fetched_lyrics, artist_name, track_name):
			print("Validation warning - using lyrics with caution")
			fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

		is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
		extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	print("[ERROR] No lyrics found")
	return None



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


			clean_line = re.sub(r'\[.*?\]|<.*?>', '', line).strip()
			if not clean_line:
				continue


			line_match = line_pattern.match(line)
			if line_match:
				current_line_time = parse_time_to_seconds(line_match.group(1))
				lyrics.append((current_line_time, None))
				
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

				lyrics.append((current_line_time, None)) 


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


def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset,time_adjust=0):
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
					if x_pos + cursor < width:# Track window dimensions
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
			status = f" Line {current_idx+1}/{len(lyrics)} "[:width-2]
			if height > 1:
				stdscr.addstr(height-1, 0, status, curses.A_BOLD)
		
		if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format:
			if height > 2:
				stdscr.addstr(height-2, 0, " End of lyrics ", curses.A_BOLD)
		
		# Add offset indicator at bottom right
	if time_adjust != 0:
		offset_str = f" Offset: {time_adjust:+.1f}s "
		offset_str = offset_str[:width-1]
		try:
			color = curses.color_pair(2) if time_adjust != 0 else curses.color_pair(3)
			stdscr.addstr(height-2, width-len(offset_str)-1, offset_str, color | curses.A_BOLD)
		except curses.error:
			pass

	# Modify existing status line to include adjustment indicator
	status_line = f" Line {current_idx+1}/{len(lyrics)} "
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
	

# def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw):
	# if key == ord('q'):
		# return False, manual_offset, last_input_time, needs_redraw
	# elif key == curses.KEY_UP:
		# manual_offset = max(0, manual_offset - 1)
		# last_input_time = time.time()
		# needs_redraw = True
	# elif key == curses.KEY_DOWN:
		# manual_offset += 1
		# last_input_time = time.time()
		# needs_redraw = True
	# elif key == curses.KEY_RESIZE:
		# needs_redraw = True
	# return True, manual_offset, last_input_time, needs_redraw
	
# Remove the first handle_scroll_input definition and keep only this one:
def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw, time_adjust):
	if key == ord('q'):
		return False, manual_offset, last_input_time, needs_redraw, time_adjust
	elif key == curses.KEY_UP:
		#time.sleep(0.01)
		manual_offset = max(0, manual_offset - 1)
		last_input_time = time.time()
		needs_redraw = True
	elif key == curses.KEY_DOWN:
		#time.sleep(0.01)
		manual_offset += 1
		last_input_time = time.time()
		needs_redraw = True
	elif key == curses.KEY_RESIZE:
		needs_redraw = True
	# elif key == ord('+'):
		# return True, manual_offset, last_input_time, needs_redraw, time_adjust + 0.5 #currently broken do not use
	# elif key == ord('-'):
		# return True, manual_offset, last_input_time, needs_redraw, time_adjust - 0.5
	
	return True, manual_offset, last_input_time, needs_redraw, time_adjust


def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, is_txt_format, is_a2_format, current_idx, manual_scroll_active, time_adjust=0):
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset=True, time_adjust=time_adjust)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset=manual_scroll_active, time_adjust=time_adjust)


# def main(stdscr):
	# curses.start_color()
	# curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	# curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	# curses.curs_set(0)
	# stdscr.timeout(200)  # Non-blocking input with 200ms timeout

	# # State variables
	# current_audio_file, current_artist, current_title = None, None, None
	# lyrics, errors = [], []
	# is_txt_format, is_a2_format = False, False
	# manual_offset, last_line_index = 0, -1
	# last_active_words, last_position = set(), -1
	# last_input_time = None
	# prev_window_size = stdscr.getmaxyx()

	# # Playback tracking
	# track_start_time = None
	# last_cmus_position = 0
	# current_duration = 0
	# time_adjust = 0.0
	# playback_paused = False

	# while True:
		# current_time = time.time()
		# needs_redraw = False
		# manual_scroll_active = last_input_time and (current_time - last_input_time < 2)
		
		# # Window resize handling (preserve scroll position)
		# current_window_size = stdscr.getmaxyx()
		# if current_window_size != prev_window_size:
			# old_height, _ = prev_window_size
			# new_height, _ = current_window_size
			# if old_height > 0 and new_height > 0:
				# manual_offset = int(manual_offset * (new_height / old_height))
			# prev_window_size = current_window_size
			# needs_redraw = True

		# # Get playback state
		# audio_file, cmus_position, artist, title, duration, status = get_cmus_info()
		# now = time.time()

		# # Track change detection
		# if audio_file != current_audio_file:
			# current_audio_file, current_artist, current_title = audio_file, artist, title
			# track_start_time = now
			# last_cmus_position = cmus_position
			# current_duration = duration
			# playback_paused = False
			# needs_redraw = True

			# # Load lyrics
			# lyrics, errors = [], []
			# if audio_file:
				# directory = os.path.dirname(audio_file)
				# artist_name = artist or "UnknownArtist"
				# track_name = title or os.path.splitext(os.path.basename(audio_file))[0]
				# lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)
				# if lyrics_file:
					# is_txt_format = lyrics_file.endswith('.txt')
					# is_a2_format = lyrics_file.endswith('.a2')
					# lyrics, errors = load_lyrics(lyrics_file)

		# # Playback state updates (without continuous prediction)
		# if cmus_position != last_cmus_position or status != ("paused" if playback_paused else "playing"):
			# last_cmus_position = cmus_position
			# was_paused = playback_paused
			# playback_paused = (status == "paused")
			# if not playback_paused and was_paused:
				# track_start_time = now
			# # No forced redraw here

		# # Use the raw cmus_position (with any manual time_adjust) as our continuous position
		# continuous_position = max(0, cmus_position + time_adjust)

		# # Determine current lyric index based on the continuous_position
		# current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], continuous_position) - 1

		# # Snap adjusted_position to the current lyric line's timestamp if available
		# if 0 <= current_idx < len(lyrics) and lyrics[current_idx][0] is not None:
			# adjusted_position = lyrics[current_idx][0]
		# else:
			# adjusted_position = continuous_position

		# # Trigger redraw only when the lyric line changes
		# if current_idx != last_line_index:
			# needs_redraw = True
			# last_line_index = current_idx

		# # A2 format word highlighting (using the snapped adjusted_position)
		# if is_a2_format and lyrics:
			# active_words = set()
			# current_line = []
			# for t, item in lyrics:
				# if item is None:
					# current_line = []
				# else:
					# current_line.append((t, item[1]))  # (end_time, word)
					# for start, (word, end) in current_line:
						# if start <= adjusted_position < end:
							# active_words.add(word)
			# if active_words != last_active_words:
				# needs_redraw = True
				# last_active_words = active_words

		# # Input handling
		# key = stdscr.getch()
		# if key != -1:
			# cont, manual_offset, last_input_time, needs_redraw_input, time_adjust = handle_scroll_input(
				# key, manual_offset, last_input_time, needs_redraw, time_adjust
			# )
			# needs_redraw |= needs_redraw_input
			# if not cont:
				# break

		# # Conditional redraw
		# if needs_redraw:
			# manual_offset = update_display(
				# stdscr, lyrics, errors, adjusted_position, audio_file, manual_offset,
				# is_txt_format, is_a2_format, current_idx, manual_scroll_active,
				# time_adjust=time_adjust
			# )
			# last_position = adjusted_position

		# time.sleep(0.01)  # Reduced CPU usage

# def main(stdscr):
	# curses.start_color()
	# curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	# curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	# curses.curs_set(0)
	# stdscr.timeout(200)  # Non-blocking input with 200ms timeout

	# # State variables
	# current_audio_file, current_artist, current_title = None, None, None
	# lyrics, errors = [], []
	# is_txt_format, is_a2_format = False, False
	# manual_offset, last_line_index = 0, -1
	# last_active_words, last_position = set(), -1
	# last_input_time = None
	# prev_window_size = stdscr.getmaxyx()

	# # Playback tracking with time estimation
	# last_cmus_position = 0
	# last_position_time = time.time()
	# estimated_position = 0
	# current_duration = 0
	# time_adjust = 0.0
	# playback_paused = False

	# while True:
		# current_time = time.time()
		# needs_redraw = False
		# manual_scroll_active = last_input_time and (current_time - last_input_time < 2)
		
		# # Window resize handling
		# current_window_size = stdscr.getmaxyx()
		# if current_window_size != prev_window_size:
			# old_height, _ = prev_window_size
			# new_height, _ = current_window_size
			# if old_height > 0 and new_height > 0:
				# manual_offset = int(manual_offset * (new_height / old_height))
			# prev_window_size = current_window_size
			# needs_redraw = True

		# # Get playback state
		# audio_file, cmus_position, artist, title, duration, status = get_cmus_info()
		# now = time.time()

		# # Update position estimation
		# if cmus_position != last_cmus_position:
			# # Reset estimation when we get new data from cmus
			# last_cmus_position = cmus_position
			# last_position_time = now
			# estimated_position = cmus_position
			# playback_paused = (status == "paused")
			
		# elif status == "playing" and not playback_paused:
			# # Estimate position based on elapsed time
			# elapsed = now - last_position_time
			# estimated_position = cmus_position + elapsed
			# estimated_position = min(estimated_position, duration)

		# # Track change detection
		# if audio_file != current_audio_file:
			# current_audio_file, current_artist, current_title = audio_file, artist, title
			# last_cmus_position = cmus_position
			# last_position_time = now
			# estimated_position = cmus_position
			# current_duration = duration
			# playback_paused = (status == "paused")
			# needs_redraw = True

			# # Load lyrics (keep existing lyrics loading code)
			# lyrics, errors = [], []
			# if audio_file:
				# directory = os.path.dirname(audio_file)
				# artist_name = artist or "UnknownArtist"
				# track_name = title or os.path.splitext(os.path.basename(audio_file))[0]
				# lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)
				# if lyrics_file:
					# is_txt_format = lyrics_file.endswith('.txt')
					# is_a2_format = lyrics_file.endswith('.a2')
					# lyrics, errors = load_lyrics(lyrics_file)

		# # Calculate continuous position with adjustment
		# continuous_position = max(0, estimated_position + time_adjust)
		# continuous_position = min(continuous_position, current_duration)

		# # Determine current lyric index
		# current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], continuous_position) - 1

		# # Snap to line if available
		# if 0 <= current_idx < len(lyrics) and lyrics[current_idx][0] is not None:
			# adjusted_position = lyrics[current_idx][0]
		# else:
			# adjusted_position = continuous_position

		# # Trigger redraw on line change
		# if current_idx != last_line_index:
			# needs_redraw = True
			# last_line_index = current_idx

		# # Input handling (keep existing code)
		# key = stdscr.getch()
		# if key != -1:
			# cont, manual_offset, last_input_time, needs_redraw_input, time_adjust = handle_scroll_input(
				# key, manual_offset, last_input_time, needs_redraw, time_adjust
			# )
			# needs_redraw |= needs_redraw_input
			# if not cont:
				# break

		# # Conditional redraw (keep existing display code)
		# if needs_redraw:
			# manual_offset = update_display(
				# stdscr, lyrics, errors, adjusted_position, audio_file, manual_offset,
				# is_txt_format, is_a2_format, current_idx, manual_scroll_active,
				# time_adjust=time_adjust
			# )
			# last_position = adjusted_position

		# time.sleep(0.01)

def main(stdscr):
	curses.start_color()
	curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	curses.curs_set(0)
	stdscr.timeout(200)  # Non-blocking input with 200ms timeout

	# State variables
	current_audio_file, current_artist, current_title = None, None, None
	lyrics, errors = [], []
	is_txt_format, is_a2_format = False, False
	manual_offset, last_line_index = 0, -1
	last_active_words, last_position = set(), -1
	last_input_time = None
	prev_window_size = stdscr.getmaxyx()
	manual_timeout_handled = True  # New state variable

	# Playback tracking with time estimation
	last_cmus_position = 0
	last_position_time = time.time()
	estimated_position = 0
	current_duration = 0
	time_adjust = 0.0
	playback_paused = False

	while True:
		try:
			current_time = time.time()
			needs_redraw = False
			time_since_input = current_time - (last_input_time or 0)  # New calculation
			
			# Detect 2-second timeout transition
			if last_input_time is not None:
				if time_since_input >= 2 and not manual_timeout_handled:
					needs_redraw = True
					manual_timeout_handled = True
				elif time_since_input < 2:
					manual_timeout_handled = False

			manual_scroll_active = last_input_time and (time_since_input < 2)

			# Window resize handling
			current_window_size = stdscr.getmaxyx()
			if current_window_size != prev_window_size:
				old_height, _ = prev_window_size
				new_height, _ = current_window_size
				if old_height > 0 and new_height > 0:
					manual_offset = max(0, int(manual_offset * (new_height / old_height)))
				prev_window_size = current_window_size
				needs_redraw = True

			# Get playback state
			audio_file, cmus_position, artist, title, duration, status = get_cmus_info()
			now = time.time()

			# Handle missing position/duration data
			if cmus_position is None or duration is None:
				cmus_position, duration = 0, 0

			# Update position estimation
			if cmus_position != last_cmus_position:
				last_cmus_position = cmus_position
				last_position_time = now
				estimated_position = cmus_position
				playback_paused = (status == "paused")

			if status == "playing" and not playback_paused:
				elapsed = now - last_position_time
				estimated_position = last_cmus_position + elapsed
				estimated_position = max(0, min(estimated_position, duration))
			elif status == "paused" and playback_paused:
				estimated_position = cmus_position
				last_position_time = now

			# Track change detection
			if audio_file != current_audio_file:
				current_audio_file, current_artist, current_title = audio_file, artist, title
				last_cmus_position = cmus_position
				last_position_time = now
				estimated_position = cmus_position
				current_duration = duration
				playback_paused = (status == "paused")
				needs_redraw = True

				# Load lyrics
				lyrics, errors = [], []
				if audio_file:
					directory = os.path.dirname(audio_file)
					artist_name = artist or "UnknownArtist"
					track_name = title or os.path.splitext(os.path.basename(audio_file))[0]
					lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)
					if lyrics_file:
						is_txt_format = lyrics_file.endswith('.txt')
						is_a2_format = lyrics_file.endswith('.a2')
						lyrics, errors = load_lyrics(lyrics_file)

			# Calculate continuous position with adjustment
			continuous_position = max(0, estimated_position + time_adjust)
			continuous_position = min(continuous_position, current_duration)

			# Determine current lyric index
			current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], continuous_position) - 1

			# Snap to line if available
			adjusted_position = lyrics[current_idx][0] if 0 <= current_idx < len(lyrics) and lyrics[current_idx][0] is not None else continuous_position

			# Trigger redraw on line change
			if current_idx != last_line_index:
				needs_redraw = True
				last_line_index = current_idx

			# Input handling
			key = stdscr.getch()
			if key != -1:
				cont, manual_offset, last_input_time, needs_redraw_input, time_adjust = handle_scroll_input(
					key, manual_offset, last_input_time, needs_redraw, time_adjust
				)
				manual_timeout_handled = False  # Reset on new input
				needs_redraw |= needs_redraw_input
				if not cont:
					break

			# Conditional redraw
			if needs_redraw:
				manual_offset = update_display(
					stdscr, lyrics, errors, adjusted_position, audio_file, manual_offset,
					is_txt_format, is_a2_format, current_idx, manual_scroll_active,
					time_adjust=time_adjust
				)
				last_position = adjusted_position

			time.sleep(0.01)
		
		except Exception as e:
			#with open("error_log.txt", "a") as f:
			#    f.write(f"Error: {str(e)}\n")
			continue

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(main)
		except KeyboardInterrupt:
			break
		except Exception as e:
			continue
		# except Exception as e:
			# with open("error_log.txt", "a") as f:
				# f.write(f"Main Error: {str(e)}\n")
			# continue
