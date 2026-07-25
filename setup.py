from setuptools import setup, find_packages

setup(
    name="airclip",
    version="1.0.0",
    description="Universal Clipboard Engine between Windows PC and iOS/Mac/Linux",
    author="Arif",
    url="https://github.com/Arif-ai/airclip",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=[
        "flask>=3.0.0",
        "pywebview>=5.0.0",
        "pillow>=10.0.0",
        "pyperclip>=1.8.2",
        "zeroconf>=0.131.0",
    ],
    entry_points={
        "console_scripts": [
            "airclip=main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
)
