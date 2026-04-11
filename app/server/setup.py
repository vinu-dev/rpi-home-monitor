from setuptools import find_packages, setup

setup(
    name="monitor-server",
    version="1.0.0",
    description="RPi Home Monitor - Server Application",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "flask>=3.0",
        "bcrypt>=4.0",
    ],
    entry_points={
        "console_scripts": [
            "monitor-server=monitor:create_app",
        ],
    },
)
