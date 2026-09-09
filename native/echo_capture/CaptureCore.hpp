// Bounded single-producer/single-consumer bridge. No DSP or echo algorithm here.
#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace dogido {
constexpr uint32_t kOutputRate = 16000;
constexpr uint32_t kChannels = 3;  // microphone, render left, render right
constexpr uint32_t kFrameSamples = 160;
constexpr size_t kCapacity = 65536;
using Frame = std::array<float, kChannels>;

enum Failure {
    kOK = 0, kTopology = 1, kDiscontinuity = 2, kOverflow = 3,
    kNonFinite = 4, kInputStopped = 5, kDeviceChanged = 6, kOutputStalled = 7,
};

class Ring {
    std::vector<Frame> data_{kCapacity};
    std::atomic<uint64_t> written_{0}, read_{0};
    size_t limit_;
public:
    explicit Ring(size_t limit) : limit_(limit) {}
    bool push(const Frame *frames, size_t count) {
        auto w = written_.load(std::memory_order_relaxed);
        auto r = read_.load(std::memory_order_acquire);
        if (count > limit_ || w - r + count > limit_ || limit_ > kCapacity) return false;
        for (size_t i = 0; i < count; ++i) data_[(w + i) % kCapacity] = frames[i];
        written_.store(w + count, std::memory_order_release);
        return true;
    }
    size_t pop(Frame *output, size_t maximum) {
        auto r = read_.load(std::memory_order_relaxed);
        auto w = written_.load(std::memory_order_acquire);
        auto count = std::min<size_t>(maximum, w - r);
        for (size_t i = 0; i < count; ++i) output[i] = data_[(r + i) % kCapacity];
        read_.store(r + count, std::memory_order_release);
        return count;
    }
};

class Timeline {
    bool started_ = false;
    double next_ = 0;
public:
    bool accept(double sampleTime, size_t count) {
        if (!std::isfinite(sampleTime) || !count) return false;
        if (started_ && std::abs(sampleTime - next_) > 1.01) return false;
        next_ = sampleTime + count;
        started_ = true;
        return true;
    }
};
}  // namespace dogido
