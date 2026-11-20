package main

import (
	"encoding/binary"
	"encoding/hex"
	"math"
	"testing"
)

func TestVideoHashGoldenVectorsMatchRelay(t *testing.T) {
	const height, width = 48, 64
	luma := make([]byte, height*width)
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			luma[y*width+x] = byte((x*3 + y*5) % 256)
		}
	}
	lumaHash, err := HashVideoLumaFrame(luma, width, height)
	if err != nil {
		t.Fatalf("HashVideoLumaFrame error: %v", err)
	}
	if got := hex.EncodeToString(lumaHash[:]); got != "c097384b5a770de9" {
		t.Fatalf("luma golden vector mismatch: got %s, want c097384b5a770de9", got)
	}

	const sampleCount = 24000
	mono := make([]float64, sampleCount)
	rawStereo := make([]byte, sampleCount*2*8)
	for i := 0; i < sampleCount; i++ {
		tm := float64(i) / float64(sampleCount)
		val := 0.6 * math.Sin(2.0*math.Pi*(220.0*tm+400.0*tm*tm))
		mono[i] = val
		binary.LittleEndian.PutUint64(rawStereo[i*16:i*16+8], math.Float64bits(val+0.125))
		binary.LittleEndian.PutUint64(rawStereo[i*16+8:i*16+16], math.Float64bits(val-0.125))
	}
	monoHash := HashVideoAudioPCM(mono)
	if got := hex.EncodeToString(monoHash[:]); got != "703f073a97dd8087" {
		t.Fatalf("audio golden vector mismatch: got %s, want 703f073a97dd8087", got)
	}
	stereoHash, err := HashInterleavedVideoAudioPCMBytes(rawStereo, 2)
	if err != nil {
		t.Fatalf("HashInterleavedVideoAudioPCMBytes error: %v", err)
	}
	if stereoHash != monoHash {
		t.Fatalf("stereo arithmetic mean hash mismatch: got %x, want %x", stereoHash, monoHash)
	}
}
