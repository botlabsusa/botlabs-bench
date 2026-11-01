"""Benchmark test modules."""
from .cpu import run_cpu_tests
from .memory import run_memory_tests
from .disk import run_disk_tests
from .network import run_network_tests
from .gpu import run_gpu_tests

ALL_TESTS = ("cpu", "memory", "disk", "network", "gpu")

__all__ = [
    "run_cpu_tests",
    "run_memory_tests",
    "run_disk_tests",
    "run_network_tests",
    "run_gpu_tests",
    "ALL_TESTS",
]
