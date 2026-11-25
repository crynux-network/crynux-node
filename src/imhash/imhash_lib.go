package main

// #define PY_SSIZE_T_CLEAN
// #include <stdlib.h>
// #include <Python.h>
// int PyArg_ParseTuple_U(PyObject*, PyObject**);
// int PyArg_ParseTuple_y_ii(PyObject*, const char**, Py_ssize_t*, int*, int*);
// int PyArg_ParseTuple_y_i(PyObject*, const char**, Py_ssize_t*, int*);
import "C"

import (
	"encoding/binary"
	"encoding/hex"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"math"
	"os"
	"unsafe"

	"github.com/corona10/goimagehash"
)

func setPythonRuntimeError(err error) {
	cErrstr := C.CString(err.Error())
	C.PyErr_SetString(C.PyExc_RuntimeError, cErrstr)
	C.free(unsafe.Pointer(cErrstr))
}

func cBytesToGoSlice(buf *C.char, bufLen C.Py_ssize_t) ([]byte, bool) {
	if bufLen < 0 || int64(bufLen) > math.MaxInt32 {
		cMsg := C.CString("byte buffer length exceeds supported range")
		C.PyErr_SetString(C.PyExc_ValueError, cMsg)
		C.free(unsafe.Pointer(cMsg))
		return nil, false
	}
	if bufLen == 0 {
		return nil, true
	}
	return C.GoBytes(unsafe.Pointer(buf), C.int(bufLen)), true
}

func hashBlockToPyBytes(block [8]byte) *C.PyObject {
	var cBuf [8]C.char
	for i, b := range block {
		cBuf[i] = C.char(b)
	}
	return C.PyBytes_FromStringAndSize(&cBuf[0], 8)
}

func imagePHash(file io.Reader) (string, error) {
	img, _, err := image.Decode(file)
	if err != nil {
		return "", err
	}
	pHash, err := goimagehash.PerceptionHash(img)
	if err != nil {
		return "", err
	}

	bs := make([]byte, pHash.Bits()/8)
	binary.BigEndian.PutUint64(bs, pHash.GetHash())

	return "0x" + hex.EncodeToString(bs), nil
}

//export getPHash
func getPHash(self *C.PyObject, args *C.PyObject) *C.PyObject {
	var obj *C.PyObject
	if C.PyArg_ParseTuple_U(args, &obj) == 0 {
		return nil
	}
	pyFileBytes := C.PyUnicode_AsUTF8String(obj)
	if pyFileBytes == nil {
		return nil
	}
	defer C.Py_DecRef(pyFileBytes)

	cFilename := C.PyBytes_AsString(pyFileBytes)
	filename := C.GoString(cFilename)
	file, err := os.Open(filename)
	if err != nil {
		setPythonRuntimeError(err)
		return nil
	}
	defer file.Close()

	hash, err := imagePHash(file)
	if err != nil {
		setPythonRuntimeError(err)
		return nil
	}
	cRetstr := C.CString(hash)
	ret := C.PyUnicode_FromString(cRetstr)
	C.free(unsafe.Pointer(cRetstr))
	return ret
}

//export getVideoFramePHash
func getVideoFramePHash(self *C.PyObject, args *C.PyObject) *C.PyObject {
	var buf *C.char
	var bufLen C.Py_ssize_t
	var width C.int
	var height C.int
	if C.PyArg_ParseTuple_y_ii(args, &buf, &bufLen, &width, &height) == 0 {
		return nil
	}
	luma, ok := cBytesToGoSlice(buf, bufLen)
	if !ok {
		return nil
	}
	block, err := HashVideoLumaFrame(luma, int(width), int(height))
	if err != nil {
		setPythonRuntimeError(err)
		return nil
	}
	return hashBlockToPyBytes(block)
}

//export getVideoAudioPHash
func getVideoAudioPHash(self *C.PyObject, args *C.PyObject) *C.PyObject {
	var buf *C.char
	var bufLen C.Py_ssize_t
	var channels C.int
	if C.PyArg_ParseTuple_y_i(args, &buf, &bufLen, &channels) == 0 {
		return nil
	}
	rawPCM, ok := cBytesToGoSlice(buf, bufLen)
	if !ok {
		return nil
	}
	block, err := HashInterleavedVideoAudioPCMBytes(rawPCM, int(channels))
	if err != nil {
		setPythonRuntimeError(err)
		return nil
	}
	return hashBlockToPyBytes(block)
}

func main() {}
