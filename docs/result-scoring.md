# Result Score Computation and the Go Perceptual Hash Extension (`imhash`)

This document specifies how `crynux_server` computes task result scores from on-disk output files, why perceptual hashing uses the `imhash` Go C extension rather than pure Python libraries, and the exact media extraction rules required to stay bit-compatible with Relay upload verification.

Related documents:

- `docs/task-system-design.md` specifies the process topology between `crynux_server` and `crynux-worker`.
- `docs/task-lifecycle.md` specifies how persisted result files and scores drive `SubmitScore` and `UploadResult`.

## 1) Node-Owned Score Computation Boundary

Score computation for all task types MUST run inside the Node process (`crynux_server`, `src/crynux_server/task/utils.py`) after the inference worker finishes writing the output artifacts to `output_dir`:

- **Worker responsibility**: `crynux-worker` MUST write only the final result files to `output_dir` (`0.png`, `1.png`, ... for image tasks; `0.json` for LLM tasks; `0.mp4` for video tasks; `validation/*.png` and `checkpoint/` for LoRA fine-tuning tasks) and return process execution metadata (`execution_dtype`, `model_cache_hash`). `crynux-worker` MUST NOT compute or return task scores.
- **Server responsibility**: `crynux_server` MUST collect the written output files in deterministic numeric order, compute the binary score from those exact on-disk files, validate that the concatenated score is a non-zero multiple of 8 bytes, and persist the file paths and score locally before calling `SubmitScore`.

Computing every score from the final on-disk artifact guarantees that the bytes hashed before `SubmitScore` are identical to the bytes uploaded to Relay during `UploadResult`. For lossy compressed outputs such as H.264 + AAC `0.mp4`, scoring pre-encoded in-memory tensors would diverge from the compressed bitstream inspected by Relay.

## 2) Why Perceptual Hashing Uses the `imhash` Go Extension

Relay is implemented in Go. During `UploadResult`, Relay recomputes the task score on CPU directly from the uploaded result file to verify that the uploaded artifact matches the committed `Score` accepted during validation:

- **Cryptographic hash (`TaskType.LLM`)**: LLM task scores are `SHA-256` digests over the raw `0.json` bytes. Because `SHA-256` is a bit-exact cryptographic function over raw bytes, Python's `hashlib.sha256` and Go's `crypto/sha256` always produce identical digests without a cross-language extension.
- **Perceptual hashes (`TaskType.SD`, `TaskType.SD_FT_LORA`, `TaskType.VIDEO`)**: Image and video task scores are 64-bit (8-byte) 2D DCT-II perceptual hashes (`pHash`), where each of the 64 low-frequency DCT coefficients is quantized to `1` or `0` by comparing it against the median of those 64 coefficients:
  - For image tasks, Relay computes `pHash` using `github.com/corona10/goimagehash` (`goimagehash.PerceptionHash`).
  - For video tasks, Relay computes `K = min(8, num_frames)` sampled frame `pHash` blocks plus an optional audio spectrogram `pHash` block in Go (`blockchain/video_hash.go`), and `VerifyUploadedVideoScore` requires exact byte equality (`bytes.Equal`, 0-bit tolerance) across all `K` visual blocks and Hamming distance at most `video_upload_audio_distance_threshold` on the audio block.
  - Implementing `pHash` independently in Python imaging or numerical libraries (such as Pillow, `imagehash`, `numpy`, or `scipy`) introduces subtle numerical differences against Go: resize kernel sampling coordinates and edge clamping, RGB-to-grayscale conversion weights and integer rounding, and floating-point summation order in 2D DCT-II. Whenever any of the 64 DCT coefficients lies near the median threshold, even a `1e-12` floating-point discrepancy flips a hash bit and causes honest nodes to fail Relay's `UploadResult` score binding check.

To eliminate cross-language numerical divergence, `crynux-node` compiles the Go perceptual hashing implementation into the `imhash` Python C extension (`src/imhash/`, built via `setuptools-golang` using Python's Stable ABI `Py_LIMITED_API`):

- `imhash.getPHash(filename)` runs `github.com/corona10/goimagehash` on PNG/JPEG files, matching Relay's image `pHash` implementation.
- `imhash.getVideoFramePHash(luma_bytes, width, height)` and `imhash.getVideoAudioPHash(pcm_f64le_bytes, channels)` execute the exact same Go `DownsampleLuma32x32`, `AudioSpectrogram32x32`, `HashInterleavedVideoAudioPCMBytes`, and `dct8x8MedianHash` functions used by Relay's `blockchain/video_hash.go`.

## 3) Cross-Platform Video Decoding and Canonical Extraction Rules

Relay runs inside a Linux container where `ffmpeg` and `ffprobe` CLI binaries are always installed, whereas `crynux-node` also ships as standalone Windows and macOS desktop packages that do not bundle external `ffmpeg` executables. `crynux_server` therefore decodes `0.mp4` in-process using `PyAV` (`av`, which ships self-contained FFmpeg shared libraries in platform wheels) without requiring `numpy` in the server environment, and passes the decoded raw buffers to `imhash`.

For `PyAV` on the Node and `ffmpeg` CLI on Relay to feed identical inputs into the Go hash functions, both sides MUST obey two extraction invariants:

1. **Raw `yuv420p` Plane-0 Luma Extraction (No `gray` Conversion)**:
   - Canonical `0.mp4` streams are encoded as 8-bit H.264 `yuv420p`, whose decoded `Y`, `U`, and `V` planes are integer bit-exact across compliant H.264 decoders.
   - Both Node (`PyAV` `frame.planes[0]`) and Relay (`ffmpeg -f rawvideo -pix_fmt yuv420p`, slicing the first `width * height` bytes per frame) MUST read plane 0 of the `yuv420p` frame directly.
   - Neither side MAY convert the decoded frame to pixel format `gray` (`frame.reformat(format="gray")` or `ffmpeg -pix_fmt gray`). Converting limited-range (`16..235`) `yuv420p` to full-range (`0..255`) `gray` invokes `libswscale` range expansion, which alters pixel values and introduces `libswscale` version- and architecture-dependent rounding differences that break Relay's 0-bit visual block equality check.
2. **Native-Channel Floating-Point PCM Decoding and Arithmetic Channel Mean (No `-ac 1` Matrix Downmix)**:
   - When `0.mp4` contains an audio stream, both Node (`PyAV` `AudioResampler(format="dbl", layout=stream.layout, rate=sample_rate)`) and Relay (`ffmpeg -vn -map 0:a:0 -f f64le -acodec pcm_f64le` without `-ac 1`) MUST decode interleaved little-endian `float64` PCM preserving the stream's native channel count $C$ and sample rate.
   - Downmixing to mono MUST be performed in Go (`HashInterleavedVideoAudioPCMBytes`) as the exact per-sample arithmetic mean $\frac{1}{C}\sum_{c=0}^{C-1} x_{i,c}$.
   - Neither side MAY use `libswresample` stereo-to-mono matrix downmixing (`ffmpeg -ac 1`), which applies unnormalized $\frac{1}{\sqrt{2}}$ weights per channel (doubling signal power and shifting log-energy values relative to $\epsilon = 10^{-10}$).
