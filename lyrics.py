import curses
import subprocess
import re
import os
import bisect
import time
import textwrap

def get_cmus_info():
    try:
        result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            return None, 0
        output = result.stdout.decode('utf-8')
    except Exception:
        return None, 0

    track_file = None
    position = 0

    track_match = re.search(r'file (.+)', output)
    position_match = re.search(r'position (\d+)', output)

    if track_match:
        track_file = track_match.group(1)
    if position_match:
        position = int(position_match.group(1))

    return track_file, position

def find_lyrics_file(audio_file, directory):
    base_name, _ = os.path.splitext(os.path.basename(audio_file))
    lrc_file = os.path.join(directory, f"{base_name}.lrc")
    txt_file = os.path.join(directory, f"{base_name}.txt")
    return lrc_file if os.path.exists(lrc_file) else (txt_file if os.path.exists(txt_file) else None)

def parse_time_to_seconds(time_str):
    minutes, seconds = time_str.split(':')
    seconds, milliseconds = seconds.split('.')
    return max(0, int(minutes) * 60 + int(seconds) + float(f"0.{milliseconds}"))

def load_lyrics(file_path):
    with open(file_path, 'r', encoding="utf-8") as f:
        lines = f.readlines()

    lyrics = []
    errors = []

    for line in lines:
        raw_line = line.rstrip('\n')  # Preserve original whitespace
        is_blank = not raw_line.strip()  # Check if line is empty/whitespace

        if is_blank:
            lyrics.append((None, ""))
            continue

        match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
        if match:
            try:
                timestamp = parse_time_to_seconds(match.group(1))
                lyric = match.group(2)  # Preserve original spacing
                lyrics.append((timestamp, lyric))
            except Exception:
                errors.append(raw_line)
                lyrics.append((None, raw_line))
        else:
            lyrics.append((None, raw_line))
            errors.append(raw_line)

    return lyrics, errors

def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, current_idx):
    height, width = stdscr.getmaxyx()
    available_lines = height - 3  # Space for header and status
    wrap_width = width - 2  # Leaving space for borders

    # Generate all wrapped lines with original indices, adding a leading space only to wrapped lines
    wrapped_lines = []
    for orig_idx, (_, lyric) in enumerate(lyrics):
        if lyric.strip():  # Only wrap non-empty lyrics
            # Wrap the line to the specified width
            lines = textwrap.wrap(lyric, wrap_width)
            # For the first line, don't add space, just add the line as is
            wrapped_lines.append((orig_idx, lines[0]))
            # For subsequent lines, add a leading space for alignment
            for line in lines[1:]:
                wrapped_lines.append((orig_idx, " " + line))
        else:
            wrapped_lines.append((orig_idx, ""))

    # Find current lyric's screen positions
    current_screen_lines = [i for i, (idx, _) in enumerate(wrapped_lines) if idx == current_idx]

    # Calculate initial scroll position
    if is_txt_format or not current_screen_lines:
        start_screen_line = manual_offset
    else:
        # Center the first occurrence of current lyric
        ideal_start = current_screen_lines[0] - available_lines // 2
        start_screen_line = ideal_start + manual_offset

    # Clamp scroll position to valid range
    max_start = max(0, len(wrapped_lines) - available_lines)
    start_screen_line = max(0, min(start_screen_line, max_start))
    end_screen_line = start_screen_line + available_lines

    stdscr.clear()
    #stdscr.addstr(0, 0, f"Now Playing: {track_info}")
    current_line_y = 1
    #current_line_y = 2
    for idx, (orig_idx, line) in enumerate(wrapped_lines[start_screen_line:end_screen_line]):
        if current_line_y >= height - 1:
            break

        # Highlight current lyric lines
        if orig_idx == current_idx:
            stdscr.attron(curses.color_pair(2))
        else:
            stdscr.attron(curses.color_pair(3))

        try:
            stdscr.addstr(current_line_y, 0, line)
        except curses.error:
            pass  # Handle edge cases near screen bottom

        stdscr.attroff(curses.color_pair(2))
        stdscr.attroff(curses.color_pair(3))
        current_line_y += 1

    # Show end status
    if current_idx == len(lyrics) - 1 and not is_txt_format:
        stdscr.addstr(height-1, 0, "End of lyrics", curses.A_BOLD)
    stdscr.refresh()


def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.curs_set(0)
    stdscr.timeout(500)

    current_audio_file = None
    lyrics = []
    errors = []
    is_txt_format = False
    last_input_time = None
    manual_offset = 0
    last_redraw = 0

    while True:
        current_time = time.time()
        needs_redraw = False

        # Auto-reset manual offset after 2 seconds
        if last_input_time and (current_time - last_input_time >= 2):
            manual_offset = 0
            last_input_time = None
            needs_redraw = True

        # Get player status
        audio_file, position = get_cmus_info()

        # Handle track changes
        if audio_file != current_audio_file:
            current_audio_file = audio_file
            manual_offset = 0
            last_input_time = None
            lyrics = []
            errors = []
            needs_redraw = True

            if audio_file:
                directory = os.path.dirname(audio_file)
                lyrics_file = find_lyrics_file(audio_file, directory)
                if lyrics_file:
                    is_txt_format = lyrics_file.endswith('.txt')
                    lyrics, errors = load_lyrics(lyrics_file)

        # Redraw logic
        if audio_file and (needs_redraw or (current_time - last_redraw >= 0.5)):
            title = os.path.basename(audio_file)
            current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
            display_lyrics(stdscr, lyrics, errors, position, title, manual_offset, is_txt_format, current_idx)
            last_redraw = current_time

        # Input handling
        key = stdscr.getch()
        if key != -1:
            last_input_time = time.time()
            if key == curses.KEY_UP:
                manual_offset -= 1
                needs_redraw = True
            elif key == curses.KEY_DOWN:
                manual_offset += 1
                needs_redraw = True
            elif key == curses.KEY_RESIZE:
                needs_redraw = True
            elif key == ord('q'):
                break

            if needs_redraw and audio_file:
                title = os.path.basename(audio_file)
                current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
                display_lyrics(stdscr, lyrics, errors, position, title, manual_offset, is_txt_format, current_idx)
                last_redraw = current_time

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        exit()
