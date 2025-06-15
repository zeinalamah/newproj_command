
from setuptools import setup

setup(
    name='newproj',
    version='0.1.0',
    py_modules=['newproj'],
    include_package_data=True,
    install_requires=[],
    entry_points={
        'console_scripts': [
            'newproj=newproj:main',
        ],
    },
    author='Your Name',
    description='CLI tool to generate standardized project directory structures.',
    python_requires='>=3.7',
)
