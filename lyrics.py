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

    if file_path.endswith('.txt'):
        for line in lines:
            raw_line = line.rstrip('\n')
            is_blank = not raw_line.strip()

            if is_blank:
                lyrics.append((None, ""))
                continue

            lyrics.append((None, " " + raw_line))
    else:
        for line in lines:
            raw_line = line.rstrip('\n')
            is_blank = not raw_line.strip()

            if is_blank:
                lyrics.append((None, ""))
                continue

            match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
            if match:
                try:
                    timestamp = parse_time_to_seconds(match.group(1))
                    lyric = match.group(2)
                    lyrics.append((timestamp, lyric))
                except Exception:
                    errors.append(raw_line)
                    lyrics.append((None, raw_line))
            else:
                lyrics.append((None, raw_line))
                errors.append(raw_line)

    return lyrics, errors

def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format, current_idx, use_manual_offset):
    height, width = stdscr.getmaxyx()
    available_lines = height - 3
    wrap_width = width - 2

    # Wrap lyrics and track original indices for each wrapped line
    wrapped_lines = []
    for orig_idx, (_, lyric) in enumerate(lyrics):
        if lyric.strip():
            lines = textwrap.wrap(lyric, wrap_width)
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
        # Find all wrapped lines belonging to current original line
        indices = [i for i, (orig, _) in enumerate(wrapped_lines) if orig == current_idx]
        if indices:
            first = indices[0]
            last = indices[-1]
            center = (first + last) // 2
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

        if orig_idx == current_idx:
            stdscr.attron(curses.color_pair(2))
        else:
            stdscr.attron(curses.color_pair(3))

        try:
            stdscr.addstr(current_line_y, 0, line)
        except curses.error:
            pass

        stdscr.attroff(curses.color_pair(2))
        stdscr.attroff(curses.color_pair(3))
        current_line_y += 1

    if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format:
        stdscr.addstr(height-1, 0, "End of lyrics", curses.A_BOLD)
    stdscr.refresh()

    return start_screen_line

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
    last_position = -1

    while True:
        current_time = time.time()
        needs_redraw = False

        if not is_txt_format and last_input_time and (current_time - last_input_time >= 2):
            last_input_time = None
            needs_redraw = True

        audio_file, position = get_cmus_info()

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

        if audio_file and (needs_redraw or (current_time - last_redraw >= 0.5) or position != last_position):
            height, width = stdscr.getmaxyx()
            available_lines = height - 3

            # Find current index based on position
            current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
            manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)

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

            last_position = position
            last_redraw = current_time

        key = stdscr.getch()

        if key == curses.KEY_UP:
            manual_offset -= 1
            last_input_time = current_time
            needs_redraw = True
        elif key == curses.KEY_DOWN:
            manual_offset += 1
            last_input_time = current_time
            needs_redraw = True
        elif key == curses.KEY_RESIZE:
            needs_redraw = True
        elif key == ord('q'):
            break

        if needs_redraw and audio_file:
            height, width = stdscr.getmaxyx()
            available_lines = height - 3

            current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
            manual_scroll_active = last_input_time is not None and (current_time - last_input_time < 2)

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

            last_redraw = current_time

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        exit()
