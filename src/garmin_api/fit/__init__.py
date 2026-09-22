"""Reading and writing FIT files for strength training.

A FIT activity file is how a completed workout gets into Garmin Connect with
its sets intact. Garmin's official developer programme has never offered an
endpoint that uploads one, so this is the route: build the file, then post it
to the same upload service the website uses.
"""

from .decode import decode_strength_session
from .encode import encode_strength_session

__all__ = ["decode_strength_session", "encode_strength_session"]
