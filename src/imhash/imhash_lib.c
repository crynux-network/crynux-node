#define Py_SSIZE_T_CLEAN
#include <Python.h>

/* Exported from Go */
PyObject* getPHash(PyObject*, PyObject*);
PyObject* getVideoFramePHash(PyObject*, PyObject*);
PyObject* getVideoAudioPHash(PyObject*, PyObject*);

/* Shims for Go's lack of variadic C function calls */
int PyArg_ParseTuple_U(PyObject* args, PyObject** obj) {
    return PyArg_ParseTuple(args, "U", obj);
}

int PyArg_ParseTuple_y_ii(PyObject* args, const char** buf, Py_ssize_t* buf_len, int* width, int* height) {
    return PyArg_ParseTuple(args, "y#ii", buf, buf_len, width, height);
}

int PyArg_ParseTuple_y_i(PyObject* args, const char** buf, Py_ssize_t* buf_len, int* channels) {
    return PyArg_ParseTuple(args, "y#i", buf, buf_len, channels);
}

static struct PyMethodDef methods[] = {
    {"getPHash", (PyCFunction)getPHash, METH_VARARGS, NULL},
    {"getVideoFramePHash", (PyCFunction)getVideoFramePHash, METH_VARARGS, NULL},
    {"getVideoAudioPHash", (PyCFunction)getVideoAudioPHash, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "imhash",
    NULL,
    -1,
    methods
};

PyMODINIT_FUNC PyInit_imhash(void) {
    return PyModule_Create(&module);
}
