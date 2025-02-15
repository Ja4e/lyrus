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

def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None):
    """Fetch lyrics with strict validation against track metadata"""
    search_term = f"{track_name} {artist_name}".strip()
    if not search_term:
        return None, None
    
    try:
        lyrics = syncedlyrics.search(search_term)
        if not lyrics:
            return None, None
            
        if not validate_lyrics(lyrics, artist_name, track_name):
            print("Lyrics validation failed - metadata mismatch")
            return None, None
            
        is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) 
                          for line in lyrics.split('\n'))
        return lyrics, is_synced
    except Exception as e:
        print(f"Error fetching lyrics: {e}")
        return None, None
	
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
	position = 0
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
	
	# Try to open the file
	try:
		with open(file_path, 'r', encoding="utf-8") as f:
			lines = f.readlines()
	except Exception as e:
		errors.append(f"Error opening file {file_path}: {str(e)}")
		return lyrics, errors
	if file_path.endswith('.txt'):
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
							errors.append(f"Invalid word timestamp: {word_time_str}")
					# Also add the line time for fallback
					lyrics.append((line_time, lyric_content.strip()))
				else:
					lyrics.append((line_time, lyric_content.strip()))
			else:
				lyrics.append((None, raw_line))
				errors.append(raw_line)
	return lyrics, errors

def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, current_idx, use_manual_offset):
	height, width = stdscr.getmaxyx()
	available_lines = height - 3
	wrap_width = width - 2

	wrapped_lines = []
	for orig_idx, (_, lyric) in enumerate(lyrics):
		if lyric.strip():
			# Wrap with current width and preserve leading space
			lines = textwrap.wrap(lyric, wrap_width, drop_whitespace=False)
			if lines:
				wrapped_lines.append((orig_idx, lines[0]))
				for line in lines[1:]:
					wrapped_lines.append((orig_idx, " " + line))  # Indent wrapped lines
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
		
		# # Add a space to the start or end of the line (without wrapping)
		# line_with_space = " " + line  # Adding a space before the line text
		
		# # Center the line in the screen width
		# padding = (width - len(line_with_space) - 2) // 2  # Calculate padding
		# centered_line = " " * padding + line_with_space

		# Trim any leading/trailing spaces for proper centering
		trimmed_line = line.strip()

		# Calculate proper padding for centering
		padding = max(0, (width - len(trimmed_line)) // 2)

		# Apply the padding correctly while preserving original spacing
		centered_line = " " * padding + trimmed_line


		if orig_idx == current_idx:
			stdscr.attron(curses.color_pair(2))
		else:
			stdscr.attron(curses.color_pair(3))

		try:
			stdscr.addstr(current_line_y, 0, centered_line)
		except curses.error:
			pass
		stdscr.attroff(curses.color_pair(2))
		stdscr.attroff(curses.color_pair(3))
		current_line_y += 1
	
	if current_idx is not None and current_idx < len(lyrics):
		# status = f"Playing: {track_info} - Line {current_idx+1}/{len(lyrics)} "
		
		status = f"Line {current_idx+1}/{len(lyrics)} "
		# Ensure status fits within screen width
		if len(status) > width - 2:
			status = status[:width - 2]
		
		if height > 1:  # Ensure there's space at the bottom
			stdscr.addstr(height-1, 0, status, curses.A_BOLD)

	if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format:
		if height > 2:  # Ensure there's space for the 'End of lyrics' message
			stdscr.addstr(height-2, 0, "End of lyrics ", curses.A_BOLD)

	stdscr.refresh()
	return start_screen_line


def main(stdscr):
	curses.start_color()
	curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
	curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
	curses.curs_set(0)
	stdscr.timeout(500)
	current_audio_file = None
	current_artist = None  # Track current artist
	current_title = None   # Track current title
	lyrics = []
	errors = []
	is_txt_format = False
	last_input_time = None
	manual_offset = 0
	last_redraw = 0
	last_position = -1
	last_line_index = -1

	while True:
		current_time = time.time()
		needs_redraw = False
		
		audio_file, position, artist, title, duration = get_cmus_info()

		if (audio_file != current_audio_file or 
			artist != current_artist or 
			title != current_title):

			# Update current tracking variables
			current_audio_file = audio_file
			current_artist = artist
			current_title = title

			# Reset lyric state
			last_line_index = -1
			manual_offset = 0
			last_input_time = None
			lyrics = []
			errors = []
			needs_redraw = True

			if audio_file:
				directory = os.path.dirname(audio_file)
				artist_name = current_artist if current_artist else "UnknownArtist"
				track_name = current_title if current_title else os.path.splitext(os.path.basename(audio_file))[0]
				lyrics_file = find_lyrics_file(audio_file, directory, artist_name, track_name, duration)
				if lyrics_file:
					is_txt_format = lyrics_file.endswith('.txt')
					lyrics, errors = load_lyrics(lyrics_file)
			current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
			manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)
			if not is_txt_format:
				new_manual_offset = display_lyrics(
					stdscr,
					lyrics,
					errors,
					position,
					os.path.basename(audio_file),
					manual_offset,
					is_txt_format,
					current_idx,
					use_manual_offset=manual_scroll_active
				)
				manual_offset = new_manual_offset
			else:
				# For txt files, allow manual scrolling but prevent auto-scroll based on position
				new_manual_offset = display_lyrics(
					stdscr,
					lyrics,
					errors,
					position,
					os.path.basename(audio_file),
					manual_offset,
					is_txt_format,
					current_idx,
					use_manual_offset=True  # Allow manual scrolling
				)
				manual_offset = new_manual_offset
			last_position = position
			last_redraw = current_time
			


		# Prevent auto-scroll for txt files, but allow manual scroll
		height, width = stdscr.getmaxyx()
		available_lines = height - 3
		current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
		manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)

		# If manual scroll is active, override the last_line_index to -1 to force redraw
		if manual_scroll_active:
			last_line_index = -1

		if audio_file and (needs_redraw or (current_time - last_redraw >= 0.1) or position != last_position):
			if current_idx != last_line_index:  # Only redraw if the line has changed (or manual scroll)
				if not is_txt_format:
					new_manual_offset = display_lyrics(
						stdscr,
						lyrics,
						errors,
						position,
						os.path.basename(audio_file),
						manual_offset,
						is_txt_format,
						current_idx,
						use_manual_offset=manual_scroll_active
					)
					manual_offset = new_manual_offset
				else:
					# For txt files, allow manual scrolling but prevent auto-scroll based on position
					new_manual_offset = display_lyrics(
						stdscr,
						lyrics,
						errors,
						position,
						os.path.basename(audio_file),
						manual_offset,
						is_txt_format,
						current_idx,
						use_manual_offset=True  # Allow manual scrolling
					)
					manual_offset = new_manual_offset

				last_line_index = current_idx  # Update last displayed line index
			last_position = position
			last_redraw = current_time

		# # Force a refresh if manual input has gone inactive for 2 seconds
		# if not manual_scroll_active and last_input_time is None and (current_time - last_redraw >= 0.5):
			# last_line_index = -1  # Reset the last displayed line index
			# needs_redraw = True

		# Force a refresh if manual input has gone inactive for 2 seconds
		if last_input_time and (current_time - last_input_time >= 2):
			# If more than 2 seconds have passed since last input
			last_line_index = -1  # Reset the last displayed line index
			needs_redraw = True
			last_input_time = None  # Reset the last input time after forcing a refresh


		key = stdscr.getch()
		if key == ord('q'):
			break
		elif key == curses.KEY_UP:
			manual_offset = max(0, manual_offset - 1)
			last_input_time = current_time
			needs_redraw = True
		elif key == curses.KEY_DOWN:
			manual_offset += 1
			last_input_time = current_time
			needs_redraw = True
		elif key == curses.KEY_RESIZE:
			last_line_index = -1  
			needs_redraw = True
		# elif key == curses.KEY_RESIZE:
			# # Force complete redraw with new dimensions
			# curses.resizeterm(*stdscr.getmaxyx())
			# last_line_index = -1  # Force re-render
			# manual_offset = 0  # Reset scroll position for txt files
			# needs_redraw = True
			# last_redraw = 0  # Force a full refresh

		# Redraw lyrics if necessary
		if needs_redraw or audio_file or position != last_position:
			if current_idx != last_line_index:  # Only redraw if the line has changed or manual scroll
				if not is_txt_format:
					new_manual_offset = display_lyrics(
						stdscr,
						lyrics,
						errors,
						position,
						os.path.basename(audio_file),
						manual_offset,
						is_txt_format,
						current_idx,
						use_manual_offset=manual_scroll_active
					)
					manual_offset = new_manual_offset
				else:
					# For txt files, allow manual scrolling but prevent auto-scroll based on position
					new_manual_offset = display_lyrics(
						stdscr,
						lyrics,
						errors,
						position,
						os.path.basename(audio_file),
						manual_offset,
						is_txt_format,
						current_idx,
						use_manual_offset=True  # Allow manual scrolling
					)
					manual_offset = new_manual_offset

				last_line_index = current_idx  # Update last displayed line index
			last_redraw = current_time
		
		if is_txt_format and needs_redraw:
			new_manual_offset = display_lyrics(
				stdscr,
				lyrics,
				errors,
				position,
				os.path.basename(audio_file),
				manual_offset,
				is_txt_format,
				current_idx,
				use_manual_offset=True  # Always allow manual scrolling for .txt files
			)
			manual_offset = new_manual_offset
			last_line_index = current_idx  # Update last displayed line index
			last_redraw = current_time

if __name__ == "__main__":
	try:
		curses.wrapper(main)
	except KeyboardInterrupt:
		exit()
