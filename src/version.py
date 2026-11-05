"""Shared schema / app version for Botlabs Bench (Windows + Linux)."""

# Bump when scoring algorithms, JSON schema, or normalization change.
APP_VERSION = "1.2.0"
BENCH_VERSION = "1.2.0"

# Notes for Compare / reports when interpreting cross-OS results.
CPU_METHOD_NOTES = (
    "CPU work: identical prime-range + burn loops on Windows and Linux; "
    "multiprocessing always uses the 'spawn' start method for parity. "
    "Minor OS differences remain (process spawn cost, scheduler, timer resolution) "
    "and can affect wall-time slightly; score = work / wall time."
)
