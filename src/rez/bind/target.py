"""
Creates the system architecture package.
"""
from __future__ import absolute_import
from rez.package_maker__ import make_package
from rez.vendor.version.version import Version
from rez.bind._utils import check_version
from rez.system import system


def bind(path, version_range=None, opts=None, parser=None):
    versions = ["AMD64", "AMD86"]
    for version in versions:
        version = Version(version)
        check_version(version, version_range)

        with make_package("target", path) as pkg:
            pkg.version = version

    return ("target", versions)
