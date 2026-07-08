from pathlib import Path

INPUT_FOLDER = Path("input")
OUTPUT_FOLDER = Path("output")

MAX_SIZE_KB = 50
START_QUALITY = 85
MIN_QUALITY = 25
MAX_WIDTH = 1200
METADATA_FOLDER = Path("metadata")
METADATA_FOLDER.mkdir(exist_ok=True)