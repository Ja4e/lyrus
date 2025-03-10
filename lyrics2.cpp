#include <ncurses.h>
#include <iostream>
#include <vector>
#include <string>
#include <regex>
#include <chrono>
#include <thread>
#include <filesystem>
#include <algorithm>
#include <curl/curl.h>
#include <future>
#include <atomic>
#include <sstream>
#include <queue>
#include <cmath>
#include <fstream>
#include <cstdlib>

namespace fs = std::filesystem;
using namespace std::chrono;

// Configuration
constexpr int UPDATE_INTERVAL_MS = 100;
constexpr int LYRIC_TIMEOUT_SEC = 5;
const std::string LYRIC_DIR = "synced_lyrics";
const std::regex TIMESTAMP_REGEX(R"(\[(\d+):(\d+\.\d+)\])");
const std::regex A2_WORD_REGEX(R"(<(\d+:\d+\.\d+)>(.*?)<(\d+:\d+\.\d+)>)");

struct LyricWord {
    double start;
    double end;
    std::string text;
};

struct LyricLine {
    double timestamp;
    std::string text;
    std::vector<LyricWord> words;
    bool is_line_marker;
};

struct TrackInfo {
    std::string file_path;
    std::string artist;
    std::string title;
    int duration;
    int position;
    bool changed = false;
};

class LyricsManager {
public:
    LyricsManager() {
        curl_global_init(CURL_GLOBAL_DEFAULT);
        fs::create_directory(LYRIC_DIR);
    }

    ~LyricsManager() {
        curl_global_cleanup();
    }

    void run() {
        init_curses();
        main_loop();
        cleanup_curses();
    }

private:
    WINDOW* main_win;
    TrackInfo current_track;
    std::vector<LyricLine> lyrics;
    std::atomic<bool> running{true};
    int manual_offset = 0;
    bool manual_scroll = false;
    steady_clock::time_point last_input;

    // Curses initialization
    void init_curses() {
        main_win = initscr();
        cbreak();
        noecho();
        keypad(main_win, TRUE);
        curs_set(0);
        start_color();
        init_pair(1, COLOR_GREEN, COLOR_BLACK);
        init_pair(2, COLOR_WHITE, COLOR_BLACK);
        init_pair(3, COLOR_RED, COLOR_BLACK);
    }

    void cleanup_curses() {
        endwin();
    }

    // Main application loop
    void main_loop() {
        while(running) {
            update_track_info();
            handle_input();
            update_display();
            std::this_thread::sleep_for(milliseconds(UPDATE_INTERVAL_MS));
        }
    }

    // Track info handling
    void update_track_info() {
        TrackInfo new_info = get_cmus_info();
        
        if(new_info.file_path != current_track.file_path || 
           new_info.artist != current_track.artist ||
           new_info.title != current_track.title) {
            
            current_track = new_info;
            current_track.changed = true;
            lyrics = load_lyrics();
        }
    }

    // Lyrics loading implementation
    std::vector<LyricLine> load_lyrics() {
        std::string local_path = find_local_lyrics();
        if(!local_path.empty()) return parse_lyrics(local_path);

        std::string fetched = fetch_lyrics_online();
        if(!fetched.empty()) {
            std::string path = save_lyrics(fetched);
            return parse_lyrics(path);
        }

        return {};
    }

    // New function: Get track info from cmus
    TrackInfo get_cmus_info() {
        TrackInfo info;
        FILE* pipe = popen("cmus-remote -Q 2>/dev/null", "r");
        if (!pipe) return info;

        char buffer[128];
        while (fgets(buffer, sizeof(buffer), pipe)) {
            std::string line(buffer);
            // Parse cmus output
            if (line.rfind("file ", 0) == 0) info.file_path = line.substr(5);
            else if (line.rfind("tag artist ", 0) == 0) info.artist = line.substr(11);
            else if (line.rfind("tag title ", 0) == 0) info.title = line.substr(10);
            else if (line.rfind("duration ", 0) == 0) info.duration = std::stoi(line.substr(9));
            else if (line.rfind("position ", 0) == 0) info.position = std::stoi(line.substr(9));
        }
        pclose(pipe);
        return info;
    }

    // New function: Find local lyrics file
    std::string find_local_lyrics() {
        fs::path audio_path(current_track.file_path);
        std::string base_name = sanitize_filename(audio_path.stem().string());
        std::string artist = sanitize_filename(current_track.artist);

        // Check multiple locations
        std::vector<fs::path> paths = {
            audio_path.parent_path() / (base_name + ".lrc"),
            audio_path.parent_path() / (base_name + ".a2"),
            audio_path.parent_path() / (base_name + ".txt"),
            fs::path(LYRIC_DIR) / (base_name + "_" + artist + ".lrc"),
            fs::path(LYRIC_DIR) / (base_name + "_" + artist + ".a2")
        };

        for (const auto& p : paths) {
            if (fs::exists(p)) return p.string();
        }
        return "";
    }

    // New function: Save lyrics to file
    std::string save_lyrics(const std::string& content) {
        std::string filename = LYRIC_DIR + "/" +
            sanitize_filename(current_track.title) + "_" +
            sanitize_filename(current_track.artist) + ".lrc";
            
        std::ofstream out(filename);
        out << content;
        return filename;
    }

    // New function: Sanitize filenames
    std::string sanitize_filename(const std::string& name) {
        static const std::regex illegal_chars("[<>:\"/\\\\|?*]");
        return std::regex_replace(name, illegal_chars, "_");
    }

    // Parsing lyrics in .lrc format
    std::vector<LyricLine> parse_lrc_format(const std::string& content) {
        std::vector<LyricLine> lines;
        std::istringstream stream(content);
        std::string line_str;
        
        while (std::getline(stream, line_str)) {
            std::smatch match;
            if (std::regex_search(line_str, match, TIMESTAMP_REGEX)) {
                lines.push_back({
                    parse_time(match[1]),
                    match.suffix().str(),
                    {},
                    false
                });
            }
        }
        return lines;
    }

    // Parsing lyrics in .txt format
    std::vector<LyricLine> parse_txt_format(const std::string& content) {
        std::vector<LyricLine> lines;
        std::istringstream stream(content);
        std::string line_str;
        
        while (std::getline(stream, line_str)) {
            lines.push_back({0.0, line_str, {}, false});
        }
        return lines;
    }

    // Time parsing utility
    double parse_time(const std::string& time_str) {
        size_t colon = time_str.find(':');
        int minutes = std::stoi(time_str.substr(0, colon));
        double seconds = std::stod(time_str.substr(colon+1));
        return minutes * 60 + seconds;
    }

    // Display management
    void update_display() {
        int current_line = find_current_line();
        draw_lyrics(current_line);
    }

    int find_current_line() {
        double position = current_track.position;
        auto it = std::lower_bound(lyrics.begin(), lyrics.end(), position,
            [](const LyricLine& line, double pos) {
                return line.timestamp < pos;
            });
        return std::distance(lyrics.begin(), it) - 1;
    }

    // Advanced drawing with word highlighting
    void draw_lyrics(int current_idx) {
        werase(main_win);
        int rows, cols;
        getmaxyx(main_win, rows, cols);

        if(lyrics.empty()) {
            mvwaddstr(main_win, 0, 0, "No lyrics available");
            wrefresh(main_win);
            return;
        }

        int start_line = calculate_start_line(current_idx, rows);
        draw_visible_lines(start_line, rows, cols, current_idx);
        draw_status_bar(rows, cols);
        wrefresh(main_win);
    }

    // Calculate start line for manual scrolling
    int calculate_start_line(int current_idx, int rows) {
        if (manual_scroll) {
            return std::max(0, std::min(manual_offset, 
                static_cast<int>(lyrics.size()) - rows + 2));
        }
        return std::max(0, current_idx - rows / 2);
    }

    // Draw visible lines
    void draw_visible_lines(int start_line, int rows, int cols, int current_idx) {
        int y = 0;
        for (size_t i = start_line; i < lyrics.size() && y < rows - 1; i++, y++) {
            int color_pair = (i == current_idx) ? 1 : 2;
            wattron(main_win, COLOR_PAIR(color_pair));
            
            std::string text = lyrics[i].text.substr(0, cols - 2);
            int x = (cols - text.size()) / 2;
            mvwaddstr(main_win, y, x, text.c_str());
            wattroff(main_win, COLOR_PAIR(color_pair));
        }
    }

    // Draw status bar
    void draw_status_bar(int rows, int cols) {
        std::stringstream status;
        status << current_track.artist << " - " << current_track.title;
        status << " [Time: " << current_track.position << "s]";
        mvwaddstr(main_win, rows - 1, 0, status.str().c_str());
    }

    // Input handling
    void handle_input() {
        int ch = wgetch(main_win);
        if (ch == 'q') running = false;
    }

    // Fetch online lyrics from a service
    std::string fetch_lyrics_online() {
        // Add logic for fetching lyrics from an online service.
        return "";
    }

    // Parse lyrics from a file
    std::vector<LyricLine> parse_lyrics(const std::string& path) {
        std::ifstream file(path);
        std::stringstream buffer;
        buffer << file.rdbuf();
        std::string content = buffer.str();

        if (path.find(".lrc") != std::string::npos) {
            return parse_lrc_format(content);
        } else if (path.find(".a2") != std::string::npos) {
            return parse_txt_format(content);
        }
        return {};
    }
};

int main() {
    LyricsManager manager;
    manager.run();
    return 0;
}
