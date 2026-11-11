"""Spike, verified on macOS 26.7.2 (2026-11-11): ScreenCaptureKit system audio, 48 kHz, 2 ch, float32 non-interleaved.
Run: uv run --no-project --python 3.11 --with pyobjc-framework-ScreenCaptureKit --with pyobjc-framework-CoreMedia
--with pyobjc-framework-Quartz --with numpy python -I sck_audio.py"""
import subprocess, threading, time
import Foundation as F, ScreenCaptureKit as S, CoreMedia as C, objc, numpy as np
state = {'buffers': 0, 'samples': 0, 'sumsq': 0.0, 'peak_rms': 0.0}
done = threading.Event()
class Output(F.NSObject):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
        if kind != S.SCStreamOutputTypeAudio: return
        try:
            desc = C.CMSampleBufferGetFormatDescription(sample)
            fmt = C.CMAudioFormatDescriptionGetStreamBasicDescription(desc)
            block = C.CMSampleBufferGetDataBuffer(sample)
            length = C.CMBlockBufferGetDataLength(block)
            status, data = C.CMBlockBufferCopyDataBytes(block, 0, length, None)
            if status: raise RuntimeError(f'CMBlockBufferCopyDataBytes: {status}')
            values = np.frombuffer(data, dtype=np.float32).astype(np.float64)
            rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0.0
            state['buffers'] += 1; state['samples'] += len(values); state['sumsq'] += float(np.sum(values * values)); state['peak_rms'] = max(state['peak_rms'], rms)
            if state['buffers'] == 1:
                print(f'format: rate={fmt[0]} channels={fmt[6]} formatID={fmt[1]} flags={fmt[2]:#x} bits={fmt[7]} (flags: float={bool(fmt[2] & 1)}, non_interleaved={bool(fmt[2] & 32)})', flush=True)
        except Exception as e:
            print(f'audio callback error: {type(e).__name__}: {e}', flush=True)
def pump(seconds, event=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end and (event is None or not event.is_set()):
        F.NSRunLoop.currentRunLoop().runUntilDate_(F.NSDate.dateWithTimeIntervalSinceNow_(0.05))
def content_done(content, error):
    state['content'] = content; state['content_error'] = error; done.set()
S.SCShareableContent.getShareableContentWithCompletionHandler_(content_done)
pump(10, done)
print(f'content_error={state.get("content_error")} completed={done.is_set()}', flush=True)
content = state.get('content')
if content is None: raise SystemExit('FAIL: no shareable content')
import Quartz
main = Quartz.CGMainDisplayID()
display = next((d for d in content.displays() if d.displayID() == main), None)
if display is None: raise SystemExit(f'FAIL: main display {main} unavailable')
filt = S.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
config = S.SCStreamConfiguration.alloc().init()
config.setCapturesAudio_(True); config.setExcludesCurrentProcessAudio_(True); config.setSampleRate_(48000); config.setChannelCount_(2)
config.setWidth_(2); config.setHeight_(2); config.setMinimumFrameInterval_(C.CMTimeMake(1, 1))
stream = S.SCStream.alloc().initWithFilter_configuration_delegate_(filt, config, None)
output = Output.alloc().init()
ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(output, S.SCStreamOutputTypeAudio, None, None)
print(f'add_output={ok} error={error}', flush=True)
if not ok: raise SystemExit('FAIL: add output')
done.clear()
def started(error):
    state['start_error'] = error; done.set()
stream.startCaptureWithCompletionHandler_(started)
pump(10, done)
print(f'startCapture completed={done.is_set()} error={state.get("start_error")}', flush=True)
if not done.is_set() or state.get('start_error') is not None: raise SystemExit('FAIL: capture did not start')
p = subprocess.Popen(['/bin/sh', '-c', 'afplay /System/Library/Sounds/Ping.aiff; afplay /System/Library/Sounds/Ping.aiff'])
pump(4)
p.wait(timeout=10)
done.clear()
def stopped(error):
    print(f'stop error={error}', flush=True); done.set()
stream.stopCaptureWithCompletionHandler_(stopped)
pump(5, done)
rms = (state['sumsq'] / state['samples']) ** 0.5 if state['samples'] else 0.0
print(f'buffers={state["buffers"]} samples={state["samples"]} RMS={rms:.8f} peak_buffer_RMS={state["peak_rms"]:.8f} afplay_exit={p.returncode}', flush=True)
print(f'PASS={state["buffers"] > 0 and state["peak_rms"] > 0.0001}', flush=True)
