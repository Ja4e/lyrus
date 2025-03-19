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
from datetime import datetime, timedelta

LYRICS_TIMEOUT_LOG = "lyrics_timeouts.log"
DEBUG_LOG = "debug.log"
LOG_RETENTION_DAYS = 10

# === Logging Functions ===
def clean_debug_log():
    """Keep debug.log to a maximum of 100 lines"""
    log_dir = os.path.join(os.getcwd(), "logs")
    log_path = os.path.join(log_dir, DEBUG_LOG)
    
    if not os.path.exists(log_path):
        return

    try:
        # Read all lines from the log file
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Keep only the last 100 lines
        if len(lines) > 100:
            new_lines = lines[-100:]
            with open(log_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
    except Exception as e:
        log_debug(f"Error cleaning debug log: {e}")

def log_debug(message):
    """Log debug messages to file"""
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, DEBUG_LOG)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} | {message}\n"
    
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        # Clean up after writing new entry
        clean_debug_log()
    except Exception as e:
        pass  # Can't log logging failures

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
                
                parts = line.split(' | ', 1)
                if len(parts) < 1:
                    continue
                
                try:
                    entry_time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
                    if entry_time >= cutoff:
                        new_lines.append(line + '\n')
                except ValueError:
                    continue

        with open(log_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
    except Exception as e:
        log_debug(f"Error cleaning log file: {e}")

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
        log_debug(f"Failed to write timeout log: {e}")

# === Core Functionality ===
def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def sanitize_string(s):
    return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

def fetch_lyrics_lrclib(artist_name, track_name, duration=None):
    base_url = "https://lrclib.net/api/get"
    params = {
        'artist_name': artist_name,
        'track_name': track_name,
    }
    if duration is not None:
        params['duration'] = duration
    try:
        response = requests.get(base_url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data.get('instrumental', False):
                return None, None
            synced_lyrics = data.get('syncedLyrics', '')
            plain_lyrics = data.get('plainLyrics', '')
            if synced_lyrics.strip():
                return synced_lyrics, True
            elif plain_lyrics.strip():
                return plain_lyrics, False
            else:
                return None, None
        elif response.status_code == 404:
            log_debug("Lyrics not found on LRCLIB.")
            return None, None
        else:
            log_debug(f"Error fetching lyrics: HTTP {response.status_code}")
            return None, None
    except Exception as e:
        log_debug(f"Error fetching lyrics from LRCLIB: {e}")
        return None, None

def parse_lrc_tags(lyrics):
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

    return norm_title in norm_content if norm_title else True or \
           norm_artist in norm_content if norm_artist else True

def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None, timeout=15):
    def worker(queue, search_term):
        try:
            lyrics = syncedlyrics.search(search_term)
            queue.put(lyrics)
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
        log_debug("Lyrics fetch timed out")
        log_timeout(artist_name, track_name)
        return None, None

    lyrics = queue.get() if not queue.empty() else None
    if not lyrics:
        log_timeout(artist_name, track_name)
        return None, None
    
    if not validate_lyrics(lyrics, artist_name, track_name):
        log_debug("Lyrics validation failed - metadata mismatch")
        return None, None

    is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
    return lyrics, is_synced

def save_lyrics(lyrics, track_name, artist_name, extension):
    folder = os.path.join(os.getcwd(), "synced_lyrics")
    os.makedirs(folder, exist_ok=True)
    sanitized_track = sanitize_filename(track_name)
    sanitized_artist = sanitize_filename(artist_name)
    
    filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
    file_path = os.path.join("synced_lyrics", filename)
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(lyrics)
        return file_path
    except Exception as e:
        log_debug(f"Error saving lyrics: {e}")
        return None

def get_cmus_info():
    try:
        result = subprocess.run(['cmus-remote', '-Q'], capture_output=True, text=True, check=True)
        output = result.stdout.splitlines()
    except subprocess.CalledProcessError:
        return None, 0, None, None, 0, "stopped"

    data_map = {
        "file": lambda x: x,
        "tag artist": lambda x: x,
        "tag title": lambda x: x,
        "status": lambda x: x,
        "duration": lambda x: int(x) if x.isdigit() else 0,
        "position": lambda x: int(x) if x.isdigit() else 0
    }

    track_file = artist = title = status = None
    position = duration = 0

    for line in output:
        key, *value = line.split(maxsplit=1)
        if key in data_map and value:
            parsed_value = data_map[key](value[0])
            if key == "file":
                track_file = parsed_value
            elif key == "tag artist":
                artist = parsed_value
            elif key == "tag title":
                title = parsed_value
            elif key == "status":
                status = parsed_value
            elif key == "duration":
                duration = parsed_value
            elif key == "position":
                position = parsed_value

    return track_file, position, artist, title, duration, status

def is_lyrics_timed_out(artist_name, track_name):
    log_dir = os.path.join(os.getcwd(), "logs")
    log_path = os.path.join(log_dir, LYRICS_TIMEOUT_LOG)

    if not os.path.exists(log_path):
        return False

    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                if artist_name and track_name:
                    if f"Artist: {artist_name}" in line and f"Title: {track_name}" in line:
                        return True
        return False
    except Exception as e:
        log_debug(f"Error checking timeout log: {e}")
        return False
    
def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
    base_name, _ = os.path.splitext(os.path.basename(audio_file))

    # Check local file variants
    local_files = [
        (os.path.join(directory, f"{base_name}.a2"), 'a2'),
        (os.path.join(directory, f"{base_name}.lrc"), 'lrc'),
        (os.path.join(directory, f"{base_name}.txt"), 'txt')
    ]

    for file_path, ext in local_files:
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if validate_lyrics(content, artist_name, track_name):
                    #log_debug(f"Using validated local .{ext} file")
                    return file_path
                else:
                    #log_debug(f"Using unvalidated local .{ext} file (fallback)")
                    return file_path
            except Exception as e:
                log_debug(f"Error reading {file_path}: {e}")

    # Check for instrumental metadata
    is_instrumental = (
        "instrumental" in track_name.lower() or 
        (artist_name and "instrumental" in artist_name.lower())
    )
    if is_instrumental:
        #log_debug("Instrumental track detected via metadata")
        return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

    # Check timeout status
    if is_lyrics_timed_out(artist_name, track_name):
        #log_debug(f"Lyrics for {artist_name} - {track_name} timed out. Skipping fetch.")
        return None

    # Fetch from syncedlyrics
    log_debug("Fetching from syncedlyrics...")
    fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
    if fetched_lyrics:
        if not validate_lyrics(fetched_lyrics, artist_name, track_name):
            log_debug("Validation warning - using lyrics with caution")
            fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

        is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
        extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
        return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
    
    # Fallback to LRCLIB
    log_debug("Fetching from LRCLIB...")
    fetched_lyrics, is_synced = fetch_lyrics_lrclib(artist_name, track_name, duration)
    if fetched_lyrics:
        extension = 'lrc' if is_synced else 'txt'
        return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
    
    log_debug("No lyrics found from any source")
    log_timeout(artist_name, track_name)
    return None

def parse_time_to_seconds(time_str):
    try:
        minutes, rest = time_str.split(':', 1)
        seconds, milliseconds = rest.split('.', 1)
        return int(minutes)*60 + int(seconds) + float(f"0.{milliseconds}")
    except ValueError:
        return 0

def load_lyrics(file_path):
    lyrics = []
    errors = []
    
    try:
        with open(file_path, 'r', encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        errors.append(f"Error opening file {file_path}: {str(e)}")
        return lyrics, errors

    if file_path.endswith('.a2'):
        current_line = []
        line_pattern = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
        word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            line_match = line_pattern.match(line)
            if line_match:
                line_time = parse_time_to_seconds(line_match.group(1))
                lyrics.append((line_time, None))
                content = line_match.group(2)
                
                words = word_pattern.findall(content)
                for start_str, text, end_str in words:
                    start = parse_time_to_seconds(start_str)
                    end = parse_time_to_seconds(end_str)
                    clean_text = re.sub(r'<.*?>', '', text).strip()
                    if clean_text:
                        lyrics.append((start, (clean_text, end)))
                
                remaining = re.sub(word_pattern, '', content).strip()
                if remaining:
                    lyrics.append((line_time, (remaining, line_time)))
                lyrics.append((line_time, None))

    elif file_path.endswith('.txt'):
        for line in lines:
            raw_line = line.rstrip('\n')
            lyrics.append((None, raw_line))
    else:
        for line in lines:
            raw_line = line.rstrip('\n')
            line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
            if line_match:
                line_time = parse_time_to_seconds(line_match.group(1))
                lyric_content = line_match.group(2)
                lyrics.append((line_time, lyric_content.strip()))
            else:
                lyrics.append((None, raw_line))

    return lyrics, errors

# === Display Functions ===
def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, is_a2_format, current_idx, use_manual_offset, time_adjust=0):
    height, width = stdscr.getmaxyx()
    start_screen_line = 0

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
        
        if time_adjust != 0:
            offset_str = f" Offset: {time_adjust:+.1f}s "
            offset_str = offset_str[:width-1]
            try:
                color = curses.color_pair(2) if time_adjust != 0 else curses.color_pair(3)
                stdscr.addstr(height-2, width-len(offset_str)-1, offset_str, color | curses.A_BOLD)
            except curses.error:
                pass

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

def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw, time_adjust):
    if key == ord('r'):
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

def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, is_txt_format, is_a2_format, current_idx, manual_scroll_active, time_adjust=0):
    if is_txt_format:
        return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, True, time_adjust)
    else:
        return display_lyrics(stdscr, lyrics, errors, position, os.path.basename(audio_file), manual_offset, is_txt_format, is_a2_format, current_idx, manual_scroll_active, time_adjust)

# === Main Application ===
def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.curs_set(0)
    stdscr.timeout(200)

    current_audio_file = None
    lyrics, errors = [], []
    is_txt_format = is_a2_format = False
    manual_offset = last_line_index = 0
    last_input_time = None
    prev_window_size = stdscr.getmaxyx()
    time_adjust = 0.0
    last_cmus_position = 0
    last_position_time = time.time()
    playback_paused = False

    while True:
        try:
            current_time = time.time()
            needs_redraw = False
            time_since_input = current_time - (last_input_time or 0)
            
            if last_input_time and time_since_input >= 2 and not hasattr(main, 'manual_timeout_handled'):
                needs_redraw = True
                main.manual_timeout_handled = True
            elif last_input_time and time_since_input < 2:
                main.manual_timeout_handled = False

            manual_scroll_active = last_input_time and (time_since_input < 2)

            current_window_size = stdscr.getmaxyx()
            if current_window_size != prev_window_size:
                old_height, _ = prev_window_size
                new_height, _ = current_window_size
                if old_height > 0 and new_height > 0:
                    manual_offset = max(0, int(manual_offset * (new_height / old_height)))
                prev_window_size = current_window_size
                needs_redraw = True

            audio_file, cmus_position, artist, title, duration, status = get_cmus_info()
            now = time.time()

            if cmus_position != last_cmus_position:
                last_cmus_position = cmus_position
                last_position_time = now
                estimated_position = cmus_position
                playback_paused = (status == "paused")

            if status == "playing" and not playback_paused:
                elapsed = now - last_position_time
                estimated_position = last_cmus_position + elapsed
                estimated_position = max(0, min(estimated_position, duration or 0))
            elif status == "paused":
                estimated_position = cmus_position
                last_position_time = now

            if audio_file != current_audio_file:
                current_audio_file, current_artist, current_title = audio_file, artist, title
                last_cmus_position = cmus_position
                last_position_time = now
                estimated_position = cmus_position
                needs_redraw = True

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

            continuous_position = max(0, estimated_position + time_adjust)
            continuous_position = min(continuous_position, duration or 0)

            current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], continuous_position) - 1
            adjusted_position = lyrics[current_idx][0] if 0 <= current_idx < len(lyrics) and lyrics[current_idx][0] is not None else continuous_position

            if current_idx != last_line_index:
                needs_redraw = True
                last_line_index = current_idx

            key = stdscr.getch()
            if key != -1:
                cont, manual_offset, last_input_time, needs_redraw_input, time_adjust = handle_scroll_input(
                    key, manual_offset, last_input_time, needs_redraw, time_adjust
                )
                main.manual_timeout_handled = False
                needs_redraw |= needs_redraw_input
                if not cont:
                    break

            if needs_redraw:
                manual_offset = update_display(
                    stdscr, lyrics, errors, adjusted_position, audio_file, manual_offset,
                    is_txt_format, is_a2_format, current_idx, manual_scroll_active,
                    time_adjust=time_adjust
                )

            time.sleep(0.01)
        
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
            log_debug(f"Wrapper error: {str(e)}")
            continue
