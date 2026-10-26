"""A process that uses an NVIDIA GPU for a while, through the CUDA driver alone.

No CUDA toolkit or Python package is needed: ``libcuda`` ships with the driver.
It holds ``MIB`` mebibytes of GPU memory and keeps the GPU busy clearing it,
for ``SECONDS`` seconds.

Usage: python gpu_workload.py SECONDS MIB
"""

from __future__ import annotations

import ctypes
import sys
import time


def main() -> int:
    seconds, mib = float(sys.argv[1]), int(sys.argv[2])
    cuda = ctypes.CDLL("libcuda.so.1")
    context = ctypes.c_void_p()
    device = ctypes.c_int()
    pointer = ctypes.c_uint64()
    size = mib * 1024 * 1024
    if (
        cuda.cuInit(0) != 0
        or cuda.cuDeviceGet(ctypes.byref(device), 0) != 0
        or cuda.cuCtxCreate_v2(ctypes.byref(context), 0, device) != 0
        or cuda.cuMemAlloc_v2(ctypes.byref(pointer), ctypes.c_size_t(size)) != 0
    ):
        return 1
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        cuda.cuMemsetD8_v2(pointer, ctypes.c_ubyte(1), ctypes.c_size_t(size))
        cuda.cuCtxSynchronize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
