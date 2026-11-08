"""Put src/ on the import path so `pytest` works from the Python/ root."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
