from setuptools import setup, find_packages
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='infersynth',
    version='0.1.1',  # Required
    description='Inference and synthesis of circuit from requirements',  # Required
    long_description=long_description,  # join from readme
    long_description_content_type='text/x-rst',
    url='https://github.com/rossvonfange/InferSynth',  # Optional
    author='Ross VonFange',
    classifiers=[
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        'Development Status :: 3 - Alpha',
        'Intended Audience :: End Users/Desktop',
        'Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    keywords='requirements electronic design automation (EDA)',
    packages=find_packages(exclude=['infersynth', 'docs', 'tests', 'data']),
    platfrom=['any'],
    install_requires=['PyQt5', 'numpy'],
    extras_require={  # Optional
        'dev': ['check-manifest', 'sphinx'],
        'test': ['coverage'],
    },

    # TODO: change this after db is implimented and preloaded with data
    # If there are data files included in your packages that need to be
    # installed, specify them here.
    # MANIFEST.in as well.
    #package_data={  # Optional
    #    'sample': ['package_data.dat'],
    #},

    # Although 'package_data' is the preferred approach, in some case you may
    # need to place data files outside of your packages. See:
    # http://docs.python.org/3.4/distutils/setupscript.html#installing-additional-files
    #
    # In this case, 'data_file' will be installed into '<sys.prefix>/my_data'
    #data_files=[('my_data', ['data/data_file'])],  # Optional

    # To provide executable scripts, use entry points in preference to the
    # "scripts" keyword. Entry points provide cross-platform support and allow
    # `pip` to create the appropriate form of executable for the target
    # platform.
    #
    # For example, the following would provide a command called `sample` which
    # executes the function `main` from this package when invoked:
    entry_points={  # Optional
        'gui_scripts': [
            'infersynth=infersynth.__main__:main',
        ],
    },
)