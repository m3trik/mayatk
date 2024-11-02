import setuptools
from mayatk import __package__, __version__
import pythontk as ptk

long_description = ptk.get_file_contents("docs/README.md")
description = ptk.get_text_between_delimiters(
    long_description,
    "<!-- short_description_start -->",
    "<!-- short_description_end -->",
    as_string=True,
)

setuptools.setup(
    name=__package__,
    version=__version__,
    author="Ryan Simpson",
    author_email="m3trik@outlook.com",
    description=description,
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=f"https://github.com/m3trik/{__package__}",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    packages=setuptools.find_packages(exclude=["pymel", "pymel.*"]),
    include_package_data=True,
    install_requires=ptk.update_requirements(exc=["pymel", "qtpy"]),
    data_files=ptk.get_dir_contents(
        __package__,
        "filepath",
        exc_files=["*.py", "*.pyc", "*.json", "*.bak"],
        recursive=True,
    ),
)


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
