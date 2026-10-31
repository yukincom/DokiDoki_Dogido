// Lifecycle stub only: no weights, inference, microphone or real audio backend.
#include "whisper.h"
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <string>
#include <thread>

struct whisper_context { std::string text; };

static void event(const std::string &text) {
    std::ofstream stream(std::getenv("DOGIDO_WHISPER_STUB_EVENTS"), std::ios::app);
    stream << text << '\n';
}

whisper_context_params whisper_context_default_params() {
    return {};
}

whisper_context *whisper_init_from_file_with_params(const char *, whisper_context_params params) {
    if (!params.flash_attn) return nullptr;
    event(params.use_gpu ? "load:gpu" : "load:cpu");
    return new whisper_context;
}

void whisper_free(whisper_context *context) {
    event("free");
    delete context;
}

void whisper_log_set(ggml_log_callback, void *) {}

whisper_full_params whisper_full_default_params(whisper_sampling_strategy strategy) {
    whisper_full_params params{};
    params.strategy = strategy;
    params.greedy.best_of = strategy == WHISPER_SAMPLING_GREEDY ? 5 : -1;
    params.beam_search.beam_size = strategy == WHISPER_SAMPLING_BEAM_SEARCH ? 5 : -1;
    params.no_context = true;
    params.temperature_inc = 0.2f;
    return params;
}

int whisper_full(whisper_context *context, whisper_full_params params, const float *samples, int count) {
    if (params.strategy != WHISPER_SAMPLING_BEAM_SEARCH || params.beam_search.beam_size != 5
        || params.greedy.best_of != 5 || params.n_threads != 4 || !params.no_context
        || std::string(params.language) != "ja" || params.print_progress || params.print_realtime
        || params.temperature_inc != 0.2f || count != 4 || samples[0] != -1.0f
        || samples[1] != 0.0f || samples[2] != 0.5f || samples[3] != 32767.0f / 32768.0f) {
        event("wrong-parameters");
        return 90;
    }
    const std::string prompt = params.initial_prompt;
    event("begin:" + prompt);
    if (!params.encoder_begin_callback(context, nullptr, params.encoder_begin_callback_user_data)) {
        event("abort:encoder");
        return 1;
    }
    if (prompt == "hold") {
        event("waiting:hold");
        while (!params.abort_callback(params.abort_callback_user_data)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        event("abort:compute");
        return 1;
    }
    context->text = prompt + ":" + std::to_string(params.no_speech_thold);
    return prompt == "partial-error" ? 1 : 0;
}

int whisper_full_n_segments(whisper_context *) { return 1; }
int64_t whisper_full_get_segment_t0(whisper_context *, int) { return 0; }
int64_t whisper_full_get_segment_t1(whisper_context *, int) { return 100; }
const char *whisper_full_get_segment_text(whisper_context *context, int) { return context->text.c_str(); }
