import os
import setuptools

from mayatk import __package__, __version__
from pythontk import File, Str


long_description = File.get_file_contents("docs/README.md")
description = Str.get_text_between_delimiters(
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
    packages=setuptools.find_packages(exclude=["pymel", "pymel.*"]),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    include_package_data=True,
    data_files=File.get_dir_contents(
        __package__, "filepaths", exc_files=["*.py", "*.pyc", "*.json"]
    ),  # ie. ('uitk/ui/0', ['uitk/ui/0/init.ui']),
)

# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# Deprecated ------------------------------------


# def gen_data_files(dirs, exc=[], inc=[]):
# 	'''
# 	'''
# 	dirs = Iter.make_list(dirs)
# 	exc = Iter.make_list(exc)
# 	inc = Iter.make_list(inc)

# 	results = []
# 	for src_dir in dirs:
# 		for root, dirs, files in os.walk(src_dir):
# 			filtered=[]
# 			for f in files:
# 				ext = File.format_path(f, 'ext')
# 				if f in exc or '*.'+ext in exc:
# 					continue
# 				if any(inc): #filter inc for None values so not to get a false positive.
# 					if not f in inc and not '*.'+ext in inc:
# 						continue
# 				filtered.append(f)

# 			if filtered:
# 				results.append((root, list(map(lambda f:root + "/" + f, filtered))))
# 	return results

# # for i in gen_data_files('uitk', exc=['*.py', '*.pyc', '*.json']):
# 	# print (i)
