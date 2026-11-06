// music_probe — one pass over a music library with libavformat.
//
// Replaces one ffprobe process per file: reads tags, audio properties and the
// embedded cover in a single walk, and prints one JSON object per line.
//
// Build:  g++ -O2 -std=c++20 $(pkg-config --cflags libavformat libavcodec libavutil) \
//             -o music_probe music_probe.cpp $(pkg-config --libs libavformat libavcodec libavutil)
// Usage:  music_probe --root DIR [--covers DIR] [--limit N]
#include <algorithm>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
}

namespace fs = std::filesystem;

static bool is_audio_ext(const std::string& ext) {
  static const char* kExts[] = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg",
                                ".opus", ".aiff", ".aif", ".alac", ".wv", ".ape"};
  for (const char* e : kExts) if (ext == e) return true;
  return false;
}

static std::string json_escape(const std::string& in) {
  std::string out;
  out.reserve(in.size() + 8);
  for (unsigned char c : in) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[7];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
  return out;
}

static std::string fnv_hex(const std::string& text) {
  uint64_t h = 1469598103934665603ULL;
  for (unsigned char c : text) { h ^= c; h *= 1099511628211ULL; }
  char buf[17];
  std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(h));
  return buf;
}

static std::string meta(AVDictionary* dict, const char* key) {
  if (!dict) return {};
  AVDictionaryEntry* entry = av_dict_get(dict, key, nullptr, 0);
  return entry && entry->value ? entry->value : "";
}

int main(int argc, char** argv) {
  std::string root;
  std::string covers;
  long limit = 0;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--root" && i + 1 < argc) root = argv[++i];
    else if (arg == "--covers" && i + 1 < argc) covers = argv[++i];
    else if (arg == "--limit" && i + 1 < argc) limit = std::stol(argv[++i]);
  }
  if (root.empty()) {
    std::fprintf(stderr, "usage: music_probe --root DIR [--covers DIR] [--limit N]\n");
    return 2;
  }
  if (!covers.empty()) fs::create_directories(covers);

  long probed = 0;
  std::error_code ec;
  for (auto it = fs::recursive_directory_iterator(root, fs::directory_options::skip_permission_denied, ec);
       it != fs::recursive_directory_iterator(); it.increment(ec)) {
    if (ec) break;
    std::error_code file_ec;
    if (!it->is_regular_file(file_ec)) continue;
    fs::path path = it->path();
    std::string ext = path.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    if (!is_audio_ext(ext)) continue;

    AVFormatContext* fmt = nullptr;
    if (avformat_open_input(&fmt, path.string().c_str(), nullptr, nullptr) < 0) continue;
    if (avformat_find_stream_info(fmt, nullptr) < 0) { avformat_close_input(&fmt); continue; }
    int audio_idx = av_find_best_stream(fmt, AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0);
    if (audio_idx < 0) { avformat_close_input(&fmt); continue; }
    AVStream* stream = fmt->streams[audio_idx];

    std::string title = meta(fmt->metadata, "title");
    if (title.empty()) title = meta(stream->metadata, "title");
    if (title.empty()) title = path.stem().string();
    std::string artist = meta(fmt->metadata, "artist");
    if (artist.empty()) artist = meta(stream->metadata, "artist");
    std::string album = meta(fmt->metadata, "album");
    std::string album_artist = meta(fmt->metadata, "album_artist");
    std::string genre = meta(fmt->metadata, "genre");
    std::string date = meta(fmt->metadata, "date");
    std::string track = meta(fmt->metadata, "track");

    std::string cover_name;
    if (!covers.empty()) {
      for (unsigned i = 0; i < fmt->nb_streams; ++i) {
        AVStream* art = fmt->streams[i];
        if (!(art->disposition & AV_DISPOSITION_ATTACHED_PIC)) continue;
        if (art->attached_pic.size <= 0 || !art->attached_pic.data) continue;
        std::string id = fnv_hex(fs::absolute(path).string());
        std::string cover_ext = art->codecpar->codec_id == AV_CODEC_ID_PNG ? ".png" : ".jpg";
        cover_name = id + cover_ext;
        std::ofstream out((fs::path(covers) / cover_name).string(), std::ios::binary);
        out.write(reinterpret_cast<const char*>(art->attached_pic.data), art->attached_pic.size);
        break;
      }
    }

    double duration = 0.0;
    if (fmt->duration > 0) duration = static_cast<double>(fmt->duration) / AV_TIME_BASE;
    else if (stream->duration > 0) duration = stream->duration * av_q2d(stream->time_base);
    int bits = stream->codecpar->bits_per_raw_sample;
    if (bits <= 0) bits = av_get_bits_per_sample(stream->codecpar->codec_id);
    int channels = stream->codecpar->ch_layout.nb_channels;
    const char* codec = avcodec_get_name(stream->codecpar->codec_id);

    std::ostringstream os;
    os << "{\"path\":\"" << json_escape(fs::relative(path, root).string()) << "\""
       << ",\"title\":\"" << json_escape(title) << "\""
       << ",\"artist\":\"" << json_escape(artist) << "\""
       << ",\"album\":\"" << json_escape(album) << "\""
       << ",\"album_artist\":\"" << json_escape(album_artist) << "\""
       << ",\"genre\":\"" << json_escape(genre) << "\""
       << ",\"date\":\"" << json_escape(date) << "\""
       << ",\"track\":\"" << json_escape(track) << "\""
       << ",\"duration_sec\":" << duration
       << ",\"sample_rate\":" << stream->codecpar->sample_rate
       << ",\"channels\":" << channels
       << ",\"bits\":" << bits
       << ",\"codec\":\"" << json_escape(codec ? codec : "") << "\"";
    if (!cover_name.empty()) os << ",\"cover\":\"" << json_escape(cover_name) << "\"";
    os << "}";
    std::cout << os.str() << "\n";
    avformat_close_input(&fmt);
    if (++probed, limit > 0 && probed >= limit) break;
  }
  std::fprintf(stderr, "probed %ld files\n", probed);
  return 0;
}
