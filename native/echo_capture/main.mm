// Audio-only, unmuted Core Audio tap + microphone on one private aggregate clock.
// No default-device changes, screen/video capture, files, network, or playback.
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <CoreAudio/CoreAudio.h>
#import <CoreAudio/AudioHardwareTapping.h>
#import <CoreAudio/CATapDescription.h>
#import <Foundation/Foundation.h>

#include "CaptureCore.hpp"
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <poll.h>
#include <stdexcept>
#include <thread>
#include <unistd.h>

using namespace dogido;
using Clock = std::chrono::steady_clock;
static_assert(std::atomic<sig_atomic_t>::is_always_lock_free, "signal flag must be lock-free");
static std::atomic<sig_atomic_t> quitting{0};
static void stopped(int) { quitting = 1; }

static void check(OSStatus status, const char *operation) {
    if (status != noErr)
        throw std::runtime_error(std::string(operation) + ": OSStatus=" + std::to_string(status));
}
static AudioObjectPropertyAddress address(AudioObjectPropertySelector selector,
        AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    return {selector, scope, kAudioObjectPropertyElementMain};
}
template <typename T> static T value(AudioObjectID id, AudioObjectPropertySelector selector,
        AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    T result{};
    auto a = address(selector, scope);
    UInt32 size = sizeof(result);
    check(AudioObjectGetPropertyData(id, &a, 0, nullptr, &size, &result), "read audio property");
    if (size != sizeof(result)) throw std::runtime_error("audio property size changed");
    return result;
}
template <typename T> static std::vector<T> values(AudioObjectID id, AudioObjectPropertySelector selector,
        AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    auto a = address(selector, scope);
    UInt32 size = 0;
    check(AudioObjectGetPropertyDataSize(id, &a, 0, nullptr, &size), "read audio array size");
    if (size > 65536 || size % sizeof(T)) throw std::runtime_error("invalid audio array size");
    std::vector<T> out(size / sizeof(T));
    if (size) check(AudioObjectGetPropertyData(id, &a, 0, nullptr, &size, out.data()), "read audio array");
    return out;
}
static NSString *stringValue(AudioObjectID id, AudioObjectPropertySelector selector) {
    CFStringRef text = value<CFStringRef>(id, selector);
    return CFBridgingRelease(text);
}
static std::vector<UInt32> inputLayout(AudioObjectID id) {
    auto a = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput);
    UInt32 size = 0;
    check(AudioObjectGetPropertyDataSize(id, &a, 0, nullptr, &size), "read input layout size");
    if (size < offsetof(AudioBufferList, mBuffers) || size > 65536)
        throw std::runtime_error("invalid input layout size");
    std::vector<uint8_t> storage(size);
    check(AudioObjectGetPropertyData(id, &a, 0, nullptr, &size, storage.data()), "read input layout");
    auto *list = reinterpret_cast<AudioBufferList *>(storage.data());
    if (list->mNumberBuffers > 32 || offsetof(AudioBufferList, mBuffers) +
            list->mNumberBuffers * sizeof(AudioBuffer) > size)
        throw std::runtime_error("invalid input buffer count");
    std::vector<UInt32> result;
    for (UInt32 i = 0; i < list->mNumberBuffers; ++i) result.push_back(list->mBuffers[i].mNumberChannels);
    return result;
}
static UInt32 channelCount(const std::vector<UInt32>& layout) {
    UInt32 total = 0;
    for (auto count : layout) total += count;
    return total;
}
static NSDictionary *formatDescription(const AudioStreamBasicDescription& f) {
    return @{@"rate": @(f.mSampleRate), @"format_id": @(f.mFormatID), @"flags": @(f.mFormatFlags),
             @"bits": @(f.mBitsPerChannel), @"channels": @(f.mChannelsPerFrame),
             @"bytes_per_frame": @(f.mBytesPerFrame), @"bytes_per_packet": @(f.mBytesPerPacket),
             @"frames_per_packet": @(f.mFramesPerPacket)};
}
static void inspectFormats(AudioObjectID device, double rate, const std::vector<UInt32>& layout) {
    NSMutableArray *formats = [NSMutableArray array], *buffers = [NSMutableArray array];
    for (auto stream : values<AudioStreamID>(device, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput))
        [formats addObject:formatDescription(value<AudioStreamBasicDescription>(stream, kAudioStreamPropertyVirtualFormat))];
    for (auto channels : layout) [buffers addObject:@(channels)];
    NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"status": @"formats_only", @"aggregate_rate": @(rate),
        @"input_layout": buffers, @"streams": formats, @"audio_callbacks_registered": @0,
        @"audio_devices_started": @0} options:0 error:nil];
    std::cout.write(static_cast<const char *>(json.bytes), json.length);
    std::cout << '\n';
}
static double validateStreams(AudioObjectID device, const std::vector<UInt32>& layout, double rate) {
    auto streams = values<AudioStreamID>(device, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput);
    if (streams.size() != layout.size()) throw std::runtime_error("unexpected aggregate stream count");
    double referenceRate = 0;
    for (size_t i = 0; i < streams.size(); ++i) {
        auto f = value<AudioStreamBasicDescription>(streams[i], kAudioStreamPropertyVirtualFormat);
        if (f.mFormatID != kAudioFormatLinearPCM || f.mBitsPerChannel != 32 ||
                !(f.mFormatFlags & kAudioFormatFlagIsFloat) || (f.mFormatFlags & kAudioFormatFlagIsBigEndian) ||
                !(f.mFormatFlags & kAudioFormatFlagIsPacked) || (f.mFormatFlags & kAudioFormatFlagIsNonInterleaved) ||
                f.mChannelsPerFrame != layout[i] || !std::isfinite(f.mSampleRate) ||
                f.mSampleRate < 8000 || f.mSampleRate > 192000 ||
                (i + 1 != streams.size() && std::abs(f.mSampleRate - rate) > .01) ||
                f.mFramesPerPacket != 1 || f.mBytesPerFrame != sizeof(float) * layout[i] ||
                f.mBytesPerPacket != f.mBytesPerFrame) {
            NSData *json = [NSJSONSerialization dataWithJSONObject:formatDescription(f) options:0 error:nil];
            throw std::runtime_error("unsupported aggregate stream " + std::to_string(i) + ": " +
                std::string(static_cast<const char *>(json.bytes), json.length));
        }
        referenceRate = f.mSampleRate;
    }
    return referenceRate;
}

struct CaptureState {
    Ring ring;
    Timeline timeline;
    std::vector<UInt32> layout;
    UInt32 micChannels;
    double rate, advertisedReferenceRate;
    std::atomic<int> failure{kOK};
    std::atomic<int> failureStage{0};  // 1=buffer frame count, 2=input timeline
    std::atomic<UInt32> observedMicFrames{0}, observedReferenceFrames{0};
    std::array<Frame, 4096> scratch{};
    CaptureState(double rate, std::vector<UInt32> layout_, UInt32 mic, double referenceRate_ = 0)
        : ring(static_cast<size_t>(rate * .25)),
          layout(std::move(layout_)), micChannels(mic), rate(rate),
          advertisedReferenceRate(referenceRate_ ? referenceRate_ : rate) {}
    void fail(int reason, int stage = 0) {
        int ok = kOK;
        if (failure.compare_exchange_strong(ok, reason)) failureStage = stage;
    }
    void accept(const AudioBufferList *input, const AudioTimeStamp *when) {
        if (failure.load() || quitting) return;
        if (!input || input->mNumberBuffers != layout.size() || !when ||
                !(when->mFlags & kAudioTimeStampSampleTimeValid)) { fail(kTopology); return; }
        size_t frames = 0;
        for (size_t b = 0; b < layout.size(); ++b) {
            const auto& buffer = input->mBuffers[b];
            auto channels = layout[b];
            if (!channels || buffer.mNumberChannels != channels || !buffer.mData ||
                    buffer.mDataByteSize % (sizeof(float) * channels)) { fail(kTopology); return; }
            auto count = buffer.mDataByteSize / (sizeof(float) * channels);
            if (!count) { fail(kTopology); return; }
            if (b + 1 == layout.size()) observedReferenceFrames = static_cast<UInt32>(count);
            else observedMicFrames = static_cast<UInt32>(count);
            if (b && count != frames) { fail(kDiscontinuity, 1); return; }
            frames = count;
        }
        // AudioDeviceIOProc supplies one current aggregate IO cycle. Every enabled
        // input buffer has the same frame count in the aggregate clock, even when a
        // tap stream property still advertises its source rate (observed: 512/512).
        if (!frames || frames > scratch.size()) { fail(kTopology); return; }
        if (!timeline.accept(when->mSampleTime, frames)) { fail(kDiscontinuity, 2); return; }
        for (size_t f = 0; f < frames; ++f) {
            Frame out{};
            UInt32 channel = 0;
            for (size_t b = 0; b < layout.size(); ++b) {
                const float *data = static_cast<const float *>(input->mBuffers[b].mData);
                for (UInt32 c = 0; c < layout[b]; ++c, ++channel) {
                    float sample = data[f * layout[b] + c];
                    if (!std::isfinite(sample)) { fail(kNonFinite); return; }
                    if (channel < micChannels) out[0] += sample / micChannels;
                    else if (channel < micChannels + 2) out[1 + channel - micChannels] = sample;
                    else { fail(kTopology); return; }
                }
            }
            scratch[f] = out;
        }
        if (!ring.push(scratch.data(), frames)) fail(kOverflow);
    }
};

static OSStatus audioInput(AudioObjectID, const AudioTimeStamp *, const AudioBufferList *input,
        const AudioTimeStamp *when, AudioBufferList *output, const AudioTimeStamp *, void *opaque) {
    // Aggregate input only. Explicit zeros for any output buffers; no audio rerouting.
    if (output) for (UInt32 b = 0; b < output->mNumberBuffers; ++b)
        if (output->mBuffers[b].mData) std::memset(output->mBuffers[b].mData, 0, output->mBuffers[b].mDataByteSize);
    static_cast<CaptureState *>(opaque)->accept(input, when);
    return noErr;
}

struct ConverterInput {
    CaptureState& state;
    std::array<Frame, 4096> scratch{};
};
static OSStatus converterInput(AudioConverterRef, UInt32 *count, AudioBufferList *data,
        AudioStreamPacketDescription **, void *opaque) {
    auto& context = *static_cast<ConverterInput *>(opaque);
    auto deadline = Clock::now() + std::chrono::seconds(2);
    size_t available = 0;
    while (!available && !quitting && !context.state.failure.load()) {
        available = context.state.ring.pop(context.scratch.data(), std::min<size_t>(*count, context.scratch.size()));
        if (available) break;
        if (Clock::now() >= deadline) { context.state.fail(kInputStopped); break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    *count = static_cast<UInt32>(available);
    data->mNumberBuffers = 1;
    data->mBuffers[0] = {kChannels, static_cast<UInt32>(available * sizeof(Frame)), context.scratch.data()};
    return available ? noErr : kAudioConverterErr_UnspecifiedError;
}
class Converter {
    AudioConverterRef converter_ = nullptr;
    ConverterInput input_;
public:
    Converter(CaptureState& state, double rate) : input_{state} {
        AudioStreamBasicDescription in{};
        in.mSampleRate = rate;
        in.mFormatID = kAudioFormatLinearPCM;
        in.mFormatFlags = kAudioFormatFlagsNativeFloatPacked;
        in.mFramesPerPacket = 1;
        in.mChannelsPerFrame = kChannels;
        in.mBitsPerChannel = 32;
        in.mBytesPerFrame = in.mBytesPerPacket = sizeof(Frame);
        auto out = in;
        out.mSampleRate = kOutputRate;
        out.mFormatFlags = kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked;
        out.mBitsPerChannel = 16;
        out.mBytesPerFrame = out.mBytesPerPacket = kChannels * 2;
        check(AudioConverterNew(&in, &out, &converter_), "create shared PCM converter");
        if (std::abs(rate - kOutputRate) > .01) {
            // Normal removes converter through-latency from all three channels together.
            UInt32 prime = kConverterPrimeMethod_Normal;
            OSStatus status = AudioConverterSetProperty(converter_, kAudioConverterPrimeMethod, sizeof(prime), &prime);
            if (status != noErr) { AudioConverterDispose(converter_); converter_ = nullptr; check(status, "set converter priming"); }
        }
    }
    ~Converter() { if (converter_) AudioConverterDispose(converter_); }
    bool next(std::array<int16_t, kFrameSamples * kChannels>& samples) {
        AudioBufferList out{};
        out.mNumberBuffers = 1;
        out.mBuffers[0] = {kChannels, static_cast<UInt32>(samples.size() * sizeof(int16_t)), samples.data()};
        UInt32 count = kFrameSamples;
        OSStatus status = AudioConverterFillComplexBuffer(converter_, converterInput, &input_, &count, &out, nullptr);
        if (quitting || input_.state.failure.load()) return false;
        check(status, "convert synchronized microphone and render PCM");
        if (count != kFrameSamples) throw std::runtime_error("incomplete converted frame");
        return true;
    }
};

static bool writeAll(const void *data, size_t size) {
    const auto *bytes = static_cast<const uint8_t *>(data);
    auto deadline = Clock::now() + std::chrono::seconds(1);
    while (size && !quitting) {
        ssize_t written = write(STDOUT_FILENO, bytes, size);
        if (written > 0) { bytes += written; size -= written; continue; }
        if (written < 0 && errno == EINTR) continue;
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && Clock::now() < deadline) {
            pollfd fd{STDOUT_FILENO, POLLOUT, 0};
            poll(&fd, 1, 20);
            continue;
        }
        return false;
    }
    return size == 0;
}
static void emitError(const std::string& message) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"reason": @"aec_failed", @"detail": @(message.c_str())}
                                                  options:0 error:nil];
    std::cerr.write(static_cast<const char *>(json.bytes), json.length);
    std::cerr << '\n';
}

class Devices {
public:
    AudioObjectID tap = kAudioObjectUnknown, aggregate = kAudioObjectUnknown;
    AudioDeviceIOProcID proc = nullptr;
    bool started = false;
    ~Devices() {
        if (started) AudioDeviceStop(aggregate, proc);
        if (proc) AudioDeviceDestroyIOProcID(aggregate, proc);
        if (aggregate != kAudioObjectUnknown) AudioHardwareDestroyAggregateDevice(aggregate);
        if (tap != kAudioObjectUnknown) AudioHardwareDestroyProcessTap(tap);
    }
};
static AudioObjectID defaultDevice(AudioObjectPropertySelector selector) {
    return value<AudioObjectID>(kAudioObjectSystemObject, selector);
}
static AudioObjectID chooseInput(NSString *uid) {
    if (!uid.length) return defaultDevice(kAudioHardwarePropertyDefaultInputDevice);
    for (auto id : values<AudioObjectID>(kAudioObjectSystemObject, kAudioHardwarePropertyDevices))
        if ([stringValue(id, kAudioDevicePropertyDeviceUID) isEqualToString:uid] && channelCount(inputLayout(id))) return id;
    throw std::runtime_error("configured input UID was not found");
}
static void listDevices() {
    NSMutableArray *list = [NSMutableArray array];
    auto defaultInput = defaultDevice(kAudioHardwarePropertyDefaultInputDevice);
    for (auto id : values<AudioObjectID>(kAudioObjectSystemObject, kAudioHardwarePropertyDevices)) {
        UInt32 count = channelCount(inputLayout(id));
        if (count) [list addObject:@{@"uid": stringValue(id, kAudioDevicePropertyDeviceUID),
            @"name": stringValue(id, kAudioObjectPropertyName), @"input_channels": @(count),
            @"default_input": @(id == defaultInput)}];
    }
    NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"inputs":list, @"audio_devices_opened":@0} options:0 error:nil];
    std::cout.write(static_cast<const char *>(json.bytes), json.length);
    std::cout << '\n';
}
static void requireMicrophonePermission() {
    auto status = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
    if (status == AVAuthorizationStatusAuthorized) return;
    if (status != AVAuthorizationStatusNotDetermined) throw std::runtime_error("microphone permission denied");
    std::cerr << "{\"reason\":\"aec_permission_requested\",\"detail\":\"マイクの使用を許可してください。\"}\n";
    auto ready = dispatch_semaphore_create(0);
    __block BOOL allowed = NO;
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) {
        allowed = granted;
        dispatch_semaphore_signal(ready);
    }];
    if (dispatch_semaphore_wait(ready, dispatch_time(DISPATCH_TIME_NOW, 25 * NSEC_PER_SEC)) || !allowed)
        throw std::runtime_error("microphone permission was not granted; restart after allowing it");
}

static int capture(NSString *requestedUID, bool inspectOnly = false) {
    if (!inspectOnly) requireMicrophonePermission();
    auto mic = chooseInput(requestedUID);
    auto output = defaultDevice(kAudioHardwarePropertyDefaultOutputDevice);
    auto systemOutput = defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice);
    auto micLayout = inputLayout(mic);
    UInt32 micChannels = channelCount(micLayout);
    if (!micChannels || micChannels > 16) throw std::runtime_error("unsupported microphone channel count");
    NSString *micUID = stringValue(mic, kAudioDevicePropertyDeviceUID);
    // Callback state must outlive Devices on every exception path after AudioDeviceStart.
    std::unique_ptr<CaptureState> stateOwner;
    Devices devices;
    auto *description = [[CATapDescription alloc] initStereoGlobalTapButExcludeProcesses:@[]];
    [description setPrivate:YES];
    description.name = @"Dogido echo reference";
    description.muteBehavior = CATapUnmuted;
    check(AudioHardwareCreateProcessTap(description, &devices.tap), "create system audio reference (permission required)");
    auto tapFormat = value<AudioStreamBasicDescription>(devices.tap, kAudioTapPropertyFormat);
    if (tapFormat.mChannelsPerFrame != 2) throw std::runtime_error("system reference is not stereo");
    NSDictionary *composition = @{
        @kAudioAggregateDeviceNameKey: @"Dogido microphone and echo reference",
        @kAudioAggregateDeviceUIDKey: [@"local.dogido.echo." stringByAppendingString:NSUUID.UUID.UUIDString],
        @kAudioAggregateDeviceIsPrivateKey: @YES,
        @kAudioAggregateDeviceIsStackedKey: @NO,
        @kAudioAggregateDeviceMainSubDeviceKey: micUID,
        @kAudioAggregateDeviceSubDeviceListKey: @[@{@kAudioSubDeviceUIDKey: micUID}],
        @kAudioAggregateDeviceTapAutoStartKey: @NO, // Do not wait for playback before capturing the microphone.
        @kAudioAggregateDeviceTapListKey: @[@{@kAudioSubTapUIDKey: description.UUID.UUIDString,
                                            @kAudioSubTapDriftCompensationKey: @YES}],
    };
    check(AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)composition, &devices.aggregate), "create private input aggregate");
    bool ready = false;
    for (int i = 0; i < 30 && !quitting; ++i) {
        if (value<UInt32>(devices.aggregate, kAudioDevicePropertyDeviceIsAlive)) { ready = true; break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!ready) throw std::runtime_error("private input aggregate did not become ready");
    auto layout = inputLayout(devices.aggregate);
    auto rate = value<Float64>(devices.aggregate, kAudioDevicePropertyNominalSampleRate);
    // Metadata only: private tap/aggregate are destroyed by Devices, without registering or starting IO.
    if (inspectOnly) { inspectFormats(devices.aggregate, rate, layout); return 0; }
    // Only the selected mic subdevice and the 2-channel tap are allowed. Unknown topology stops.
    if (channelCount(layout) != micChannels + 2 || layout.size() != micLayout.size() + 1 ||
            !std::equal(micLayout.begin(), micLayout.end(), layout.begin()) || layout.back() != 2)
        throw std::runtime_error("microphone/render channel topology differs from the supported layout");
    if (rate < 8000 || rate > 192000 || !std::isfinite(rate)) throw std::runtime_error("unsupported aggregate rate");
    auto referenceRate = validateStreams(devices.aggregate, layout, rate);
    stateOwner = std::make_unique<CaptureState>(rate, layout, micChannels, referenceRate);
    auto& state = *stateOwner;
    Converter converter(state, rate);
    check(AudioDeviceCreateIOProcID(devices.aggregate, audioInput, &state, &devices.proc), "create input callback");
    check(AudioDeviceStart(devices.aggregate, devices.proc), "start microphone and system audio capture");
    devices.started = true;
    int flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK)) throw std::runtime_error("cannot configure output pipe");
    // One aggregate IO cycle aligns every input buffer to the main-device clock.
    // A single persistent conversion keeps microphone and stereo render aligned.
    std::atomic<bool> workerDone{false};
    std::thread worker([&] {
        @autoreleasepool {
            try {
                std::array<int16_t, kFrameSamples * kChannels> pcm{};
                bool headerSent = false;
                while (!quitting && !state.failure.load() && converter.next(pcm)) {
                    if (!headerSent) {
                        const uint8_t header[16] = {'D','G','A','E','C','1','\r','\n', 0x80,0x3e,0,0, 3,0, 16,0};
                        if (!writeAll(header, sizeof(header))) { state.fail(kOutputStalled); break; }
                        headerSent = true;
                    }
                    if (!writeAll(pcm.data(), sizeof(pcm))) { state.fail(kOutputStalled); break; }
                }
            } catch (const std::exception& error) { emitError(error.what()); state.fail(kInputStopped); }
            workerDone = true;
        }
    });
    try {
        while (!quitting && !state.failure.load() && !workerDone.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            if ((!requestedUID.length && defaultDevice(kAudioHardwarePropertyDefaultInputDevice) != mic) ||
                    defaultDevice(kAudioHardwarePropertyDefaultOutputDevice) != output ||
                    defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice) != systemOutput ||
                    !value<UInt32>(mic, kAudioDevicePropertyDeviceIsAlive) ||
                    !value<UInt32>(devices.aggregate, kAudioDevicePropertyDeviceIsAlive) ||
                    std::abs(value<Float64>(devices.aggregate, kAudioDevicePropertyNominalSampleRate) - rate) > .01)
                state.fail(kDeviceChanged);
            if (inputLayout(devices.aggregate) != layout) state.fail(kTopology);
            if (std::abs(validateStreams(devices.aggregate, layout, rate) - referenceRate) > .01)
                state.fail(kDeviceChanged);
        }
    } catch (const std::exception& error) { emitError(error.what()); state.fail(kDeviceChanged); }
    worker.join();
    // Stop callbacks while state is still alive (member teardown otherwise happens after this scope).
    AudioDeviceStop(devices.aggregate, devices.proc);
    devices.started = false;
    AudioDeviceDestroyIOProcID(devices.aggregate, devices.proc);
    devices.proc = nullptr;
    if (state.failure.load() && !quitting) {
        const char *stage = state.failureStage.load() == 1 ? "frame_counts" :
                            state.failureStage.load() == 2 ? "input_timeline" : "other";
        emitError("capture stopped; code=" + std::to_string(state.failure.load()) +
            "; stage=" + stage +
            "; mic=" + std::to_string(state.observedMicFrames.load()) + "@" + std::to_string(rate) +
            "; render=" + std::to_string(state.observedReferenceFrames.load()) + "@" + std::to_string(referenceRate) +
            "; no raw fallback");
        return 2;
    }
    return 0;
}

static void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
static void selfTest() {
    Ring ring(4);
    std::array<Frame, 5> frames{};
    for (size_t i = 0; i < frames.size(); ++i) frames[i] = {float(i), float(i + 10), float(i + 20)};
    require(!ring.push(frames.data(), 5), "ring overflow accepted");
    require(ring.push(frames.data(), 4), "ring push failed");
    Frame out[4];
    require(ring.pop(out, 2) == 2 && out[1] == frames[1], "ring order mismatch");
    require(ring.push(frames.data(), 2), "ring wrap failed");
    require(ring.pop(out, 4) == 4 && out[0] == frames[2] && out[3] == frames[1], "ring wrap order mismatch");
    Timeline timeline;
    require(timeline.accept(100, 160) && timeline.accept(260, 160), "continuous clock rejected");
    require(!timeline.accept(500, 160), "clock discontinuity accepted");
    // Exercise callback mapping and fail-closed paths without opening an audio device.
    struct TwoBuffers { UInt32 count; AudioBuffer buffers[2]; } input{};
    std::array<float, 160> mic{};
    std::array<float, 320> stereo{};
    mic.fill(.125f);
    for (size_t i = 0; i < 160; ++i) { stereo[2 * i] = .5f; stereo[2 * i + 1] = -.5f; }
    input.count = 2;
    input.buffers[0] = {1, sizeof(mic), mic.data()};
    input.buffers[1] = {2, sizeof(stereo), stereo.data()};
    AudioTimeStamp when{};
    when.mFlags = kAudioTimeStampSampleTimeValid;
    CaptureState mapped(16000, {1, 2}, 1);
    mapped.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    Frame mappedOut{};
    require(!mapped.failure.load() && mapped.ring.pop(&mappedOut, 1) == 1 &&
            mappedOut == Frame{.125f, .5f, -.5f}, "callback lost stereo reference");
    when.mSampleTime = 900;
    mapped.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    require(mapped.failure.load() == kDiscontinuity && mapped.failureStage.load() == 2,
            "callback accepted time jump or lost timeline diagnosis");
    CaptureState missing(16000, {1, 2}, 1);
    input.buffers[1].mData = nullptr;
    missing.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    require(missing.failure.load() == kTopology, "callback fabricated missing reference");
    input.buffers[1].mData = stereo.data();
    CaptureState nonFinite(16000, {1, 2}, 1);
    mic[0] = NAN;
    nonFinite.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    require(nonFinite.failure.load() == kNonFinite, "callback accepted NaN");
    mic[0] = .125f;
    CaptureState overflow(8000, {1, 2}, 1);
    for (int i = 0; i < 13; ++i) {
        when.mSampleTime = i * 160;
        overflow.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    }
    require(overflow.failure.load() == kOverflow, "callback accepted stale backlog");
    for (double rate : {16000., 48000.}) {
        CaptureState state(rate, {1, 2}, 1);
        std::vector<Frame> input(static_cast<size_t>(rate / 10), Frame{.1f, .2f, -.3f});
        require(state.ring.push(input.data(), input.size()), "converter fixture push failed");
        Converter converter(state, rate);
        std::array<int16_t, kFrameSamples * kChannels> pcm{};
        require(converter.next(pcm), "converter returned no frame");
        require(std::abs(pcm[90] - 3277) < 150 && std::abs(pcm[91] - 6554) < 150 &&
            std::abs(pcm[92] + 9830) < 150, "converter mixed channels or scaled incorrectly");
    }
    // Reproduce the live IO shape: metadata says 16 kHz mic + 48 kHz tap, while
    // one aggregate callback contains the SAME current-cycle frame count per buffer.
    CaptureState mixed(16000, {1, 2}, 1, 48000);
    std::array<float, 160> near{};
    std::array<float, 320> far{};
    for (size_t chunk = 0; chunk < 10; ++chunk) {
        near.fill(0); far.fill(0);
        for (size_t f = 0; f < near.size(); ++f)
            if (chunk * 160 + f == 500) near[f] = .8f;
        for (size_t f = 0; f < far.size() / 2; ++f)
            if (chunk * 160 + f == 500) { far[2 * f] = .8f; far[2 * f + 1] = -.8f; }
        input.buffers[0] = {1, sizeof(near), near.data()};
        input.buffers[1] = {2, sizeof(far), far.data()};
        when.mSampleTime = chunk * 160;
        mixed.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    }
    require(!mixed.failure.load(), "equal aggregate IO cycles rejected due to stream metadata rate");
    Converter mixedConverter(mixed, 16000);
    int nearPeak = -1, farPeak = -1, nearMax = 0, farMax = 0;
    for (int chunk = 0; chunk < 8; ++chunk) {
        std::array<int16_t, kFrameSamples * kChannels> pcm{};
        require(mixedConverter.next(pcm), "aggregate converter stalled");
        for (int f = 0; f < int(kFrameSamples); ++f) {
            int n = std::abs(int(pcm[3 * f])), r = std::abs(int(pcm[3 * f + 1]));
            if (n > nearMax) { nearMax = n; nearPeak = chunk * kFrameSamples + f; }
            if (r > farMax) { farMax = r; farPeak = chunk * kFrameSamples + f; }
            require(std::abs(int(pcm[3 * f + 1]) + int(pcm[3 * f + 2])) <= 1,
                    "mixed converter destroyed inverse stereo");
        }
    }
    require(nearMax > 1000 && farMax > 1000 && nearPeak == 500 && std::abs(nearPeak - farPeak) <= 1,
            "mixed converters shifted microphone/reference impulses");
    CaptureState unequal(16000, {1, 2}, 1, 48000);
    input.buffers[1].mDataByteSize = 159 * 2 * sizeof(float);
    when.mSampleTime = 0;
    unequal.accept(reinterpret_cast<AudioBufferList *>(&input), &when);
    require(unequal.failure.load() == kDiscontinuity && unequal.failureStage.load() == 1,
            "unequal aggregate IO frame counts accepted or lost count diagnosis");
    struct ThreeBuffers { UInt32 count; AudioBuffer buffers[3]; } emptyMic{};
    emptyMic.count = 3;
    emptyMic.buffers[0] = {1, 0, near.data()};
    emptyMic.buffers[1] = {1, sizeof(near), near.data()};
    emptyMic.buffers[2] = {2, sizeof(far), far.data()};
    CaptureState missingFirstMic(16000, {1, 1, 2}, 2, 48000);
    missingFirstMic.accept(reinterpret_cast<AudioBufferList *>(&emptyMic), &when);
    require(missingFirstMic.failure.load() == kTopology, "empty first microphone buffer accepted");
    std::cout << "{\"status\":\"passed\",\"audio_devices_opened\":0,\"checks\":\"ring_timeline_callback_stereo_failures_aggregate_io_alignment\"}\n";
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        signal(SIGINT, stopped);
        signal(SIGTERM, stopped);
        signal(SIGPIPE, SIG_IGN);
        try {
            bool doCapture = false, list = false, test = false, inspect = false;
            NSString *uid = @"";
            for (int i = 1; i < argc; ++i) {
                if (!strcmp(argv[i], "--self-test")) test = true;
                else if (!strcmp(argv[i], "--list-devices")) list = true;
                else if (!strcmp(argv[i], "--inspect-formats")) inspect = true;
                else if (!strcmp(argv[i], "--capture-system-audio")) doCapture = true;
                else if (!strcmp(argv[i], "--input-uid") && i + 1 < argc) uid = @(argv[++i]);
                else throw std::runtime_error("usage: --self-test | --list-devices | --inspect-formats | --capture-system-audio [--input-uid UID]");
            }
            if (int(doCapture) + int(list) + int(test) + int(inspect) != 1) throw std::runtime_error("choose exactly one operation");
            if (test) { selfTest(); return 0; }
            if (list) { listDevices(); return 0; }
            if (@available(macOS 14.2, *)) return capture(uid, inspect);
            throw std::runtime_error("Core Audio process taps require macOS 14.2 or later");
        } catch (const std::exception& error) { emitError(error.what()); return 2; }
    }
}
