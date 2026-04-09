from setuptools import setup, find_packages

setup(
    name="camera-streamer",
    version="1.0.0",
    description="RPi Home Monitor - Camera Streaming Application",
    packages=find_packages(),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "camera-streamer=camera_streamer.main:main",
        ],
    },
)
