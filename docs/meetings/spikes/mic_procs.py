"""Spike, verified on macOS 26.7.2 (2026-11-11): per-process mic use through CoreAudio process objects (macOS 14+).
Device-level 'gone' (IsRunningSomewhere) stayed 1 throughout, so only the per-process 'piri' flag is usable.
Run: uv run --no-project --python 3.11 --with sounddevice python -I mic_procs.py"""
import ctypes as c, os, subprocess, sys, time
ca = c.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
cf = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
class Address(c.Structure):
    _fields_ = [('selector', c.c_uint32), ('scope', c.c_uint32), ('element', c.c_uint32)]
def four(s): return int.from_bytes(s.encode(), 'big')
def read(obj, key, typ):
    a = Address(four(key), four('glob'), 0); v = typ(); n = c.c_uint32(c.sizeof(v))
    err = ca.AudioObjectGetPropertyData(obj, c.byref(a), 0, None, c.byref(n), c.byref(v))
    if err: raise RuntimeError(f'{key} object={obj}: OSStatus {err}')
    return v.value
def processes():
    a = Address(four('prs#'), four('glob'), 0); size = c.c_uint32()
    err = ca.AudioObjectGetPropertyDataSize(1, c.byref(a), 0, None, c.byref(size))
    if err: raise RuntimeError(f'prs# size: OSStatus {err}')
    arr = (c.c_uint32 * (size.value // 4))()
    err = ca.AudioObjectGetPropertyData(1, c.byref(a), 0, None, c.byref(size), arr)
    if err: raise RuntimeError(f'prs#: OSStatus {err}')
    result = []
    for obj in arr[:size.value // 4]:
        try:
            pid = read(obj, 'ppid', c.c_int32); active = read(obj, 'piri', c.c_uint32)
            ref = read(obj, 'pbid', c.c_void_p)
            buf = c.create_string_buffer(1024)
            cf.CFStringGetCString.argtypes = [c.c_void_p, c.c_char_p, c.c_long, c.c_uint32]
            bundle = buf.value.decode() if ref and cf.CFStringGetCString(ref, buf, len(buf), 0x08000100) else ''
            if ref: cf.CFRelease(c.c_void_p(ref))
            result.append((pid, bundle, active))
        except RuntimeError as e: print('process error:', e, flush=True)
    return sorted(result)
def snapshot(label, dev, child=None):
    rows = processes(); print(f'{label}: device_running={read(dev, "gone", c.c_uint32)}', flush=True)
    for pid, bundle, active in rows: print(f'  pid={pid} bundle={bundle!r} input={active}', flush=True)
    if child is not None: print(f'  child_active={any(pid == child and active == 1 for pid, _, active in rows)}', flush=True)
    return any(pid == child and active == 1 for pid, _, active in rows)
dev = read(1, 'dIn ', c.c_uint32)
print(f'parent_pid={os.getpid()} default_input={dev}', flush=True)
snapshot('before', dev)
p = subprocess.Popen([sys.executable, '-I', '-c', 'import sounddevice as s,time; x=s.InputStream(); x.start(); print("started",flush=True); time.sleep(4); x.stop(); x.close()'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
print(f'child_pid={p.pid}', flush=True)
line = p.stdout.readline().strip(); print(f'child: {line}', flush=True)
during = snapshot('during', dev, p.pid) if line == 'started' else False
out, err = p.communicate(); print(f'child_exit={p.returncode} stdout={out.strip()!r} stderr={err.strip()!r}', flush=True)
time.sleep(0.5)
after = snapshot('after', dev, p.pid)
print(f'PASS={during and not after}', flush=True)
