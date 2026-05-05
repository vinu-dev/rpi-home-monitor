# REQ: SWR-046, SWR-071; RISK: RISK-019, RISK-027; SEC: SC-018, SC-026; TEST: TC-043, TC-056
from setuptools import find_packages, setup

setup(
    name="monitor-server",
    version="1.0.0",
    description="RPi Home Monitor - Server Application",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "flask>=3.1.3",
        "bcrypt>=4.0",
        "jinja2>=3.0",
        "pyotp>=2.9",
        "zeroconf>=0.100",
        "boto3>=1.34",
    ],
    entry_points={
        "console_scripts": [
            "monitor-server=monitor:create_app",
        ],
    },
)
