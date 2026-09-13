"""
Starts only the Payment Service.

Usage:
    python scripts/run_payment_only.py
"""
import subprocess
import sys

if __name__ == "__main__":
    subprocess.run([
        sys.executable, "-m", "uvicorn", "payment_service.main:app",
        "--port", "8003", "--host", "127.0.0.1", "--reload",
    ])
