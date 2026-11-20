package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"sort"
)

const (
	videoDCTGridSize       = 32
	videoDCTLowFreqSize    = 8
	videoAudioTimeSegments = 32
	videoAudioFreqBands    = 32
	videoAudioWindowSize   = 64
	videoAudioMaxWindows   = 64
	videoAudioLogEpsilon   = 1e-10
)

var (
	videoDCT2DLowBasis [videoDCTLowFreqSize][videoDCTGridSize]float64
	videoAudioHann     [videoAudioWindowSize]float64
	videoAudioDCTBasis [videoAudioFreqBands][videoAudioWindowSize]float64
)

func init() {
	for u := 0; u < videoDCTLowFreqSize; u++ {
		scale := math.Sqrt(2.0 / float64(videoDCTGridSize))
		if u == 0 {
			scale = math.Sqrt(1.0 / float64(videoDCTGridSize))
		}
		for k := 0; k < videoDCTGridSize; k++ {
			videoDCT2DLowBasis[u][k] = scale * math.Cos(math.Pi*float64(2*k+1)*float64(u)/float64(2*videoDCTGridSize))
		}
	}
	for n := 0; n < videoAudioWindowSize; n++ {
		videoAudioHann[n] = 0.5 * (1.0 - math.Cos(2.0*math.Pi*float64(n)/float64(videoAudioWindowSize)))
	}
	for band := 0; band < videoAudioFreqBands; band++ {
		for n := 0; n < videoAudioWindowSize; n++ {
			videoAudioDCTBasis[band][n] = math.Cos(math.Pi * float64(2*n+1) * float64(band) / float64(2*videoAudioWindowSize))
		}
	}
}

func DownsampleLuma32x32(luma []byte, width, height int) ([videoDCTGridSize][videoDCTGridSize]float64, error) {
	var grid [videoDCTGridSize][videoDCTGridSize]float64
	if width <= 0 || height <= 0 {
		return grid, errors.New("invalid luma dimensions")
	}
	if len(luma) != width*height {
		return grid, fmt.Errorf("luma byte length %d does not match %dx%d", len(luma), width, height)
	}
	for r := 0; r < videoDCTGridSize; r++ {
		y0 := (r * height) / videoDCTGridSize
		y1 := ((r + 1) * height) / videoDCTGridSize
		if y1 <= y0 {
			y1 = y0 + 1
			if y1 > height {
				y1 = height
			}
		}
		for c := 0; c < videoDCTGridSize; c++ {
			x0 := (c * width) / videoDCTGridSize
			x1 := ((c + 1) * width) / videoDCTGridSize
			if x1 <= x0 {
				x1 = x0 + 1
				if x1 > width {
					x1 = width
				}
			}
			var sum int64
			for y := y0; y < y1; y++ {
				rowOffset := y * width
				for x := x0; x < x1; x++ {
					sum += int64(luma[rowOffset+x])
				}
			}
			count := (y1 - y0) * (x1 - x0)
			grid[r][c] = float64(sum) / float64(count)
		}
	}
	return grid, nil
}

func dct8x8MedianHash(grid [videoDCTGridSize][videoDCTGridSize]float64) [8]byte {
	var rowDCT [videoDCTGridSize][videoDCTLowFreqSize]float64
	for r := 0; r < videoDCTGridSize; r++ {
		for v := 0; v < videoDCTLowFreqSize; v++ {
			acc := 0.0
			for c := 0; c < videoDCTGridSize; c++ {
				acc += grid[r][c] * videoDCT2DLowBasis[v][c]
			}
			rowDCT[r][v] = acc
		}
	}

	var coeffs [64]float64
	var sorted [64]float64
	idx := 0
	for u := 0; u < videoDCTLowFreqSize; u++ {
		for v := 0; v < videoDCTLowFreqSize; v++ {
			acc := 0.0
			for r := 0; r < videoDCTGridSize; r++ {
				acc += videoDCT2DLowBasis[u][r] * rowDCT[r][v]
			}
			coeffs[idx] = acc
			sorted[idx] = acc
			idx++
		}
	}
	sort.Float64s(sorted[:])
	median := (sorted[31] + sorted[32]) / 2.0

	var bits uint64
	for j, val := range coeffs {
		if val > median {
			bits |= uint64(1) << uint(63-j)
		}
	}
	var out [8]byte
	binary.BigEndian.PutUint64(out[:], bits)
	return out
}

func HashVideoLumaFrame(luma []byte, width, height int) ([8]byte, error) {
	grid, err := DownsampleLuma32x32(luma, width, height)
	if err != nil {
		return [8]byte{}, err
	}
	return dct8x8MedianHash(grid), nil
}

func AudioSpectrogram32x32(monoSamples []float64) [videoDCTGridSize][videoDCTGridSize]float64 {
	var matrix [videoDCTGridSize][videoDCTGridSize]float64
	total := len(monoSamples)
	if total == 0 {
		emptyVal := math.Log(videoAudioLogEpsilon)
		for s := 0; s < videoAudioTimeSegments; s++ {
			for b := 0; b < videoAudioFreqBands; b++ {
				matrix[s][b] = emptyVal
			}
		}
		return matrix
	}

	for segIdx := 0; segIdx < videoAudioTimeSegments; segIdx++ {
		start := (segIdx * total) / videoAudioTimeSegments
		end := ((segIdx + 1) * total) / videoAudioTimeSegments
		if end <= start {
			end = start + 1
			if end > total {
				end = total
			}
		}
		segment := monoSamples[start:end]
		segLen := len(segment)

		var starts []int
		if segLen <= videoAudioWindowSize {
			starts = []int{0}
		} else {
			maxStart := segLen - videoAudioWindowSize
			numWin := segLen / videoAudioWindowSize
			if numWin < 1 {
				numWin = 1
			}
			if numWin > videoAudioMaxWindows {
				numWin = videoAudioMaxWindows
			}
			starts = make([]int, numWin)
			if numWin == 1 {
				starts[0] = 0
			} else {
				for w := 0; w < numWin; w++ {
					starts[w] = (w * maxStart) / (numWin - 1)
				}
			}
		}

		var powerSum [videoAudioFreqBands]float64
		var windowed [videoAudioWindowSize]float64
		for _, pos := range starts {
			for n := 0; n < videoAudioWindowSize; n++ {
				sample := 0.0
				if pos+n < segLen {
					sample = segment[pos+n]
				}
				windowed[n] = sample * videoAudioHann[n]
			}
			for band := 0; band < videoAudioFreqBands; band++ {
				proj := 0.0
				for n := 0; n < videoAudioWindowSize; n++ {
					proj += windowed[n] * videoAudioDCTBasis[band][n]
				}
				powerSum[band] += proj * proj
			}
		}
		winCount := float64(len(starts))
		for band := 0; band < videoAudioFreqBands; band++ {
			matrix[segIdx][band] = math.Log(powerSum[band]/winCount + videoAudioLogEpsilon)
		}
	}
	return matrix
}

func HashVideoAudioPCM(monoSamples []float64) [8]byte {
	return dct8x8MedianHash(AudioSpectrogram32x32(monoSamples))
}

func HashInterleavedVideoAudioPCMBytes(rawPCM []byte, channels int) ([8]byte, error) {
	if channels <= 0 {
		return [8]byte{}, fmt.Errorf("audio channels must be positive, got %d", channels)
	}
	frameByteWidth := channels * 8
	if len(rawPCM)%frameByteWidth != 0 {
		return [8]byte{}, fmt.Errorf("decoded PCM byte length %d is not a multiple of %d (%d channels * 8 bytes)", len(rawPCM), frameByteWidth, channels)
	}
	sampleFrames := len(rawPCM) / frameByteWidth
	mono := make([]float64, sampleFrames)
	invChannels := 1.0 / float64(channels)
	for i := 0; i < sampleFrames; i++ {
		base := i * frameByteWidth
		sum := 0.0
		for ch := 0; ch < channels; ch++ {
			offset := base + ch*8
			sum += math.Float64frombits(binary.LittleEndian.Uint64(rawPCM[offset : offset+8]))
		}
		mono[i] = sum * invChannels
	}
	return HashVideoAudioPCM(mono), nil
}
