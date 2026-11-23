// One resident whisper.cpp model; Rust owns recording, request order and results.
// [遅延短縮計画 Part 1] docs/2026-11-23-response-latency-reduction-plan.md
// 旧: Whisperモデルだけを常駐させ、Silero VADはRust側から毎回別プロセス（whisper-vad-speech-segments）を起動している。
// 新: 起動引数に任意の --vad-model を追加して起動時に1回だけVADモデルを常駐ロードし、VAD判定要求をこのワーカーで処理できるようにする。
// 意図: 毎発話のVADプロセス起動・モデル再読込（約550ms）をなくし、既存のtranscribe/cancel/shutdownプロトコルとfail-open方針を維持する。
#include "whisper.h"
#include "json.hpp"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

struct Request {
    int64_t id;
    std::string op;
    std::string wav;
    std::string prompt;
    float no_speech_threshold = 0.6f;
    float vad_threshold = 0.5f;
    int min_speech_duration_ms = 250;
    int min_silence_duration_ms = 500;
    int speech_pad_ms = 200;
    std::atomic<bool> cancelled{false};
};

struct Inbox {
    std::mutex mutex;
    std::condition_variable changed;
    std::deque<std::shared_ptr<Request>> queue;
    std::map<int64_t, std::shared_ptr<Request>> pending;
    bool stopped = false;

    void stop() {
        std::lock_guard<std::mutex> lock(mutex);
        stopped = true;
        for (auto &entry : pending) entry.second->cancelled.store(true);
        changed.notify_one();
    }
};

static void receive(Inbox &inbox) {
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            const auto frame = json::parse(line);
            const auto op = frame.at("op").get<std::string>();
            if (op == "shutdown") break;
            const auto id = frame.at("id").get<int64_t>();
            std::lock_guard<std::mutex> lock(inbox.mutex);
            if (op == "cancel") {
                const auto found = inbox.pending.find(id);
                if (found != inbox.pending.end()) found->second->cancelled.store(true);
            } else if (op == "transcribe") {
                auto request = std::make_shared<Request>();
                request->id = id;
                request->op = op;
                request->wav = frame.at("wav").get<std::string>();
                request->prompt = frame.at("prompt").get<std::string>();
                request->no_speech_threshold = frame.at("no_speech_threshold").get<float>();
                // Register at receipt: a cancel arriving before dequeue must survive.
                inbox.pending.emplace(id, request);
                inbox.queue.push_back(std::move(request));
                inbox.changed.notify_one();
            } else if (op == "vad") {
                auto request = std::make_shared<Request>();
                request->id = id;
                request->op = op;
                request->wav = frame.at("wav").get<std::string>();
                request->vad_threshold = frame.value("threshold", 0.5f);
                request->min_speech_duration_ms = frame.value("min_speech_duration_ms", 250);
                request->min_silence_duration_ms = frame.value("min_silence_duration_ms", 500);
                request->speech_pad_ms = frame.value("speech_pad_ms", 200);
                inbox.pending.emplace(id, request);
                inbox.queue.push_back(std::move(request));
                inbox.changed.notify_one();
            } else {
                throw std::runtime_error("unknown operation");
            }
        } catch (const std::exception &) {
            std::cerr << "invalid worker request\n";
            break;
        }
    }
    inbox.stop();
}

static std::vector<float> read_wav(const std::string &path) {
    // Rust writes exactly 44 header bytes followed by 16 kHz mono LE i16 PCM.
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) throw std::runtime_error("cannot open request audio");
    const auto length = stream.tellg();
    if (length < std::streamoff(44)) throw std::runtime_error("cannot read request audio");
    const auto size = static_cast<size_t>(length - std::streamoff(44));
    std::vector<unsigned char> bytes(size);
    stream.seekg(44);
    if (!stream.read(reinterpret_cast<char *>(bytes.data()), size)) {
        throw std::runtime_error("cannot read request audio");
    }
    std::vector<float> samples(size / 2);
    for (size_t i = 0; i < samples.size(); ++i) {
        const uint16_t raw = uint16_t(bytes[2 * i]) | (uint16_t(bytes[2 * i + 1]) << 8);
        const int32_t value = raw < 0x8000 ? raw : int32_t(raw) - 0x10000;
        samples[i] = static_cast<float>(value) / 32768.0f;
    }
    return samples;
}

static std::string timestamp(int64_t ticks) {
    const int64_t ms = ticks * 10;
    std::ostringstream out;
    out << std::setfill('0') << std::setw(2) << ms / 3600000 << ':'
        << std::setw(2) << (ms / 60000) % 60 << ':'
        << std::setw(2) << (ms / 1000) % 60 << '.' << std::setw(3) << ms % 1000;
    return out.str();
}

static json transcribe(whisper_context *context, Request &request) {
    json reply = {{"id", request.id}, {"success", false}, {"stdout", ""}, {"stderr", ""}};
    try {
        if (request.cancelled.load()) {
            reply["stderr"] = "cancelled";
            return reply;
        }
        const auto samples = read_wav(request.wav);
        // Match the existing whisper-cli defaults, including beam search and fallback.
        auto params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
        params.beam_search.beam_size =
            whisper_full_default_params(WHISPER_SAMPLING_BEAM_SEARCH).beam_search.beam_size;
        params.strategy = params.beam_search.beam_size > 1
            ? WHISPER_SAMPLING_BEAM_SEARCH : WHISPER_SAMPLING_GREEDY;
        params.n_threads = 4;
        params.language = "ja";
        params.no_context = true;
        params.print_realtime = false;
        params.print_progress = false;
        params.initial_prompt = request.prompt.c_str();
        params.no_speech_thold = request.no_speech_threshold;
        params.encoder_begin_callback = [](whisper_context *, whisper_state *, void *data) {
            return !static_cast<Request *>(data)->cancelled.load();
        };
        params.encoder_begin_callback_user_data = &request;
        params.abort_callback = [](void *data) {
            return static_cast<Request *>(data)->cancelled.load();
        };
        params.abort_callback_user_data = &request;
        const int result = whisper_full(context, params, samples.data(), static_cast<int>(samples.size()));
        if (request.cancelled.load()) {
            reply["stderr"] = "cancelled";
        } else {
            // CLIも途中まで出力済みの本文は非ゼロ終了時に残す。採否は既存Rust側が扱う。
            if (result != 0) reply["stderr"] = "transcription failed";
            std::ostringstream transcript;
            for (int i = 0; i < whisper_full_n_segments(context); ++i) {
                transcript << '[' << timestamp(whisper_full_get_segment_t0(context, i))
                    << " --> " << timestamp(whisper_full_get_segment_t1(context, i))
                    << "]  " << whisper_full_get_segment_text(context, i) << '\n';
            }
            reply["success"] = result == 0;
            reply["stdout"] = transcript.str();
        }
    } catch (const std::exception &) {
        // Do not put file paths, audio or the prompt into diagnostic output.
        reply["stderr"] = "cannot process request audio";
    }
    return reply;
}

static json detect_vad(whisper_vad_context *vctx, const Request &request) {
    json reply = {{"id", request.id}, {"success", false}, {"segments", 0}, {"stderr", ""}};
    try {
        if (request.cancelled.load()) {
            reply["stderr"] = "cancelled";
            return reply;
        }
        if (!vctx) {
            reply["stderr"] = "vad model not loaded";
            return reply;
        }
        const auto samples = read_wav(request.wav);
        auto params = whisper_vad_default_params();
        params.threshold = request.vad_threshold;
        params.min_speech_duration_ms = request.min_speech_duration_ms;
        params.min_silence_duration_ms = request.min_silence_duration_ms;
        params.speech_pad_ms = request.speech_pad_ms;
        auto *segments = whisper_vad_segments_from_samples(
            vctx, params, samples.data(), static_cast<int>(samples.size()));
        if (!segments) {
            reply["stderr"] = "vad failed";
            return reply;
        }
        reply["segments"] = whisper_vad_segments_n_segments(segments);
        reply["success"] = true;
        whisper_vad_free_segments(segments);
    } catch (const std::exception &) {
        reply["stderr"] = "cannot process request audio";
    }
    return reply;
}

int main(int argc, char **argv) {
    std::string model;
    std::string vad_model;
    bool use_gpu = true;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--model" && i + 1 < argc) model = argv[++i];
        else if (arg == "--vad-model" && i + 1 < argc) vad_model = argv[++i];
        else if (arg == "--no-gpu") use_gpu = false;
        else {
            std::cerr << "usage: dogido-whisper-worker --model PATH [--vad-model PATH] [--no-gpu]\n";
            return 2;
        }
    }
    if (model.empty()) {
        std::cerr << "model path is required\n";
        return 2;
    }
    auto params = whisper_context_default_params();
    params.use_gpu = use_gpu;
    params.flash_attn = true;
    auto *context = whisper_init_from_file_with_params(model.c_str(), params);
    if (!context) {
        std::cerr << "model initialization failed\n";
        return 1;
    }
    whisper_vad_context *vad_context = nullptr;
    if (!vad_model.empty()) {
        auto vad_params = whisper_vad_default_context_params();
        vad_context = whisper_vad_init_from_file_with_params(vad_model.c_str(), vad_params);
        if (!vad_context) {
            // VADモデル初期化失敗でもWhisper本体の常駐起動は継続し、Rust側のフォールバックへ委ねる。
            std::cerr << "vad model initialization failed\n";
        }
    }
    json ready = {{"ready", true}};
    if (!vad_model.empty()) {
        ready["vad"] = (vad_context != nullptr);
    }
    std::cout << ready.dump() << std::endl;
    Inbox inbox;
    std::thread reader([&] { receive(inbox); });
    while (true) {
        std::shared_ptr<Request> request;
        {
            std::unique_lock<std::mutex> lock(inbox.mutex);
            inbox.changed.wait(lock, [&] { return inbox.stopped || !inbox.queue.empty(); });
            if (inbox.stopped) break;
            request = inbox.queue.front();
            inbox.queue.pop_front();
        }
        auto reply = request->op == "vad"
            ? detect_vad(vad_context, *request)
            : transcribe(context, *request);
        {
            std::lock_guard<std::mutex> lock(inbox.mutex);
            inbox.pending.erase(request->id);
            if (inbox.stopped) break;
            // An arriving cancel can still win after whisper_full returned.
            if (request->cancelled.load()) {
                reply["success"] = false;
                if (reply.contains("stdout")) reply["stdout"] = "";
                reply["stderr"] = "cancelled";
            }
        }
        std::cout << reply.dump(-1, ' ', false, json::error_handler_t::replace) << std::endl;
    }
    reader.join();
    if (vad_context) whisper_vad_free(vad_context);
    whisper_free(context);
    return 0;
}
