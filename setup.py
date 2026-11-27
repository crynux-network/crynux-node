from __future__ import annotations

from setuptools import Extension
from setuptools import setup

setup(
    ext_modules=[
        Extension(
            "imhash",
            ["src/imhash/imhash_lib.go"],
            py_limited_api=True,
            define_macros=[("Py_LIMITED_API", "0x030A0000")],
        ),
    ],
    build_golang={"root": "imhash"},
)
