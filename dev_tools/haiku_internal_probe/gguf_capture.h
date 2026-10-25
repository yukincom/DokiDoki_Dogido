#pragma once

// 固定revisionのnativeサーバーから呼ぶ採取器。採用済みtokenの層出力を保存する。
// モデル計算・サンプリング・保存句との結合は各既存所有者が担当する。
#include "../../src/llama-ext.h"
#include "log.h"
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
extern "C" {
#include "hash/sha256/sha256.h"
}

struct dogido_capture {
    std::filesystem::path directory;
    std::ofstream hidden;
    json manifest;
    size_t limit = 256;
    int layers = 0;
    int width = 0;
    bool active = false;
    bool finalized = false;
    sha256_t digest;

    static std::unique_ptr<dogido_capture> begin(
            const server_task & task, const common_params & params,
            const llama_model * model, bool synthetic) {
        if (!task.params.dogido_capture) {
            return nullptr;
        }
        const char * root = std::getenv("DOGIDO_CAPTURE_DIR");
        if (!root || !*root) {
            LOG_WRN("dogido_capture: DOGIDO_CAPTURE_DIR is unset; capture unavailable\n");
            return nullptr;
        }
        auto result = std::make_unique<dogido_capture>();
        try {
            const auto & id = task.params.oaicompat_cmpl_id;
            if (id.empty() || id.size() > 160 || id.find_first_not_of(
                        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos) {
                throw std::runtime_error("unsafe or missing response id");
            }
            std::filesystem::path base(root);
            if (!base.is_absolute()) {
                throw std::runtime_error("capture root must be absolute");
            }
            std::filesystem::create_directories(base);
            result->directory = base / id;
            if (!std::filesystem::create_directory(result->directory)) {
                throw std::runtime_error("capture directory already exists");
            }
            result->layers = llama_model_n_layer(model);
            result->width = llama_model_n_embd(model);
            const auto prompt = task.tokens.get_text_tokens();
            result->manifest = {
                {"schema", "dogido.internal_capture.v1"}, {"backend", "llama.cpp"},
                {"response_id", id}, {"choice_index", task.index}, {"status", "recording"},
                {"boundary", "decoder_layer_output_pre_final_norm"},
                {"dtype", "float32-le"}, {"hidden_file", "hidden.f32"},
                {"shape", json::array({0, result->layers, result->width})},
                {"prompt_token_ids", prompt}, {"predicted_token_ids", json::array()},
                {"positions", json::array()}, {"total_generated_tokens", 0},
                {"model", {{"path", params.model.path}, {"name", task.params.oaicompat_model}}},
                {"source_base", "6190802c8d3391ee908fb254d12fc243ad4540c6"},
                {"implementation", "dogido-gguf-capture-v1"},
                {"generation_settings", task.params.to_json()},
            };
            sha256_init(&result->digest);
            result->hidden.open(result->directory / "hidden.f32", std::ios::binary | std::ios::out);
            result->hidden.exceptions(std::ios::failbit | std::ios::badbit);
            if (const char * cap = std::getenv("DOGIDO_CAPTURE_MAX_POSITIONS")) {
                size_t end = 0;
                result->limit = std::stoul(cap, &end);
                if (cap[end] || result->limit < 1 || result->limit > 4096) {
                    throw std::runtime_error("capture limit must be 1..4096");
                }
            }
            if (task.params.dogido_capture_full) {
                result->limit = 0;
                result->manifest["max_positions"] = nullptr;
            } else {
                result->manifest["max_positions"] = result->limit;
            }
            // Identity is prepared once by the launcher, including weight/template hashes.
            const char * identity_path = std::getenv("DOGIDO_CAPTURE_IDENTITY");
            if (!identity_path) {
                throw std::runtime_error("capture identity file is required; use launch_gguf_capture.py");
            }
            std::ifstream identity_file(identity_path);
            json identity;
            identity = json::parse(std::string(std::istreambuf_iterator<char>(identity_file), {}));
            if (identity.at("model").at("path") != params.model.path ||
                    identity.at("model").at("size_bytes") != std::filesystem::file_size(params.model.path) ||
                    !identity.at("model").at("sha256").is_string()) {
                throw std::runtime_error("capture model identity mismatch");
            }
            result->manifest["identity"] = identity;
            char arch[80] = {};
            llama_model_meta_val_str(model, "general.architecture", arch, sizeof(arch));
            const uint32_t endian = 1;
            if (*(const uint8_t *) &endian != 1 || std::string(arch) != "gemma4" ||
                    result->layers != 30 || result->width != 2816) {
                throw std::runtime_error("capture supports Gemma4 26B A4B, 30 layers, width 2816 only");
            }
            if (params.n_parallel != 1 || task.params.n_cmpl != 1 || task.index != 0 ||
                    !params.lora_adapters.empty() || !params.control_vectors.empty() || synthetic ||
                    std::any_of(params.speculative.types.begin(), params.speculative.types.end(), [](auto type) {
                        return type != COMMON_SPECULATIVE_TYPE_NONE && type != COMMON_SPECULATIVE_TYPE_DRAFT_MTP;
                    }) ||
                    task.tokens.find_next_media_chunk(0).first != nullptr ||
                    task.params.n_cache_reuse != 0 || prompt.empty()) {
                throw std::runtime_error("capture requires one text slot, no adapters, synthetic draft or cache shifting");
            }
            result->active = true;
            result->save_manifest();
        } catch (const std::exception & e) {
            result->fail(e.what());
        }
        return result;
    }

    void save_manifest() const {
        if (directory.empty() || manifest.is_null()) {
            return;
        }
        std::ofstream output(directory / "manifest.tmp", std::ios::out | std::ios::trunc);
        output.exceptions(std::ios::failbit | std::ios::badbit);
        output << manifest.dump(2) << '\n';
        output.close();
        std::filesystem::rename(directory / "manifest.tmp", directory / "manifest.json");
    }

    void fail(const std::string & error) noexcept {
        active = false;
        LOG_WRN("dogido_capture: %s\n", error.c_str());
        try {
            if (manifest.is_object()) {
                manifest["status"] = "failed";
                manifest["error"] = error;
                save_manifest();
            }
        } catch (const std::exception & e) {
            LOG_WRN("dogido_capture: manifest write failed: %s\n", e.what());
        }
    }

    void append(llama_context * ctx, int row, int batch_size, int input_position,
                llama_token predicted, int generation_index) noexcept {
        if (finalized || !manifest.is_object()) {
            return;
        }
        manifest["total_generated_tokens"] = generation_index + 1;
        if (!active || (limit != 0 && manifest["positions"].size() >= limit)) {
            return;
        }
        try {
            if (row < 0 || row >= batch_size || generation_index != (int) manifest["positions"].size() ||
                    input_position != (int) manifest["prompt_token_ids"].size() + generation_index - 1) {
                throw std::runtime_error("prediction row is not aligned with the original prompt and accepted tokens");
            }
            std::vector<float> values((size_t) layers * width);
            for (int il = 0; il < layers; ++il) {
                // Layer i+1's input is block i's output; slot 30 is pre-final-norm.
                const float * src = llama_get_embeddings_layer_inp(ctx, il + 1) + (size_t) row * width;
                if (!std::all_of(src, src + width, [](float value) { return std::isfinite(value); })) {
                    throw std::runtime_error("nonfinite or uncomputed activation row");
                }
                std::copy_n(src, width, values.data() + (size_t) il * width);
            }
            hidden.write(reinterpret_cast<const char *>(values.data()), values.size() * sizeof(float));
            sha256_update(&digest, reinterpret_cast<const unsigned char *>(values.data()), values.size() * sizeof(float));
            manifest["predicted_token_ids"].push_back(predicted);
            manifest["positions"].push_back({{"generation_index", generation_index},
                {"input_position", input_position}, {"predicted_token_id", predicted}});
            manifest["shape"][0] = manifest["positions"].size();
        } catch (const std::exception & e) {
            fail(e.what());
        }
    }

    void finish(const std::string & text, const std::string & reason, bool completed) noexcept {
        if (finalized) {
            return;
        }
        finalized = true;
        if (!manifest.is_object()) {
            return;
        }
        try {
            if (hidden.is_open()) {
                hidden.close();
            }
            manifest["rendered_text"] = text;
            manifest["finish_reason"] = reason;
            manifest["hidden_bytes"] = std::filesystem::file_size(directory / "hidden.f32");
            unsigned char hash[SHA256_DIGEST_SIZE];
            sha256_final(&digest, hash);
            const char * hex = "0123456789abcdef";
            std::string hash_hex;
            for (unsigned char byte : hash) {
                hash_hex.push_back(hex[byte >> 4]);
                hash_hex.push_back(hex[byte & 15]);
            }
            manifest["hidden_sha256"] = hash_hex;
            if (manifest["status"] != "failed") {
                manifest["status"] = !completed ? "cancelled" :
                    manifest["positions"].size() < manifest["total_generated_tokens"].get<size_t>() ? "partial" : "complete";
            }
            save_manifest();
        } catch (const std::exception & e) {
            fail(e.what());
        }
    }
};
