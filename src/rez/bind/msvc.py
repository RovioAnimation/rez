"""
Binds a MSVC executable as a rez package.
"""
from __future__ import absolute_import
from rez.package_maker__ import make_package
from rez.bind._utils import check_version, find_exe, extract_version, make_dirs
from rez.utils.platform_ import platform_
from rez.system import system
from rez.utils.lint_helper import env
import os.path

def enumerate_msvc(is_win64):
    # inspired by SCons MSCommon tool
    # https://bitbucket.org/scons/scons/src/6ccc44a210aea2a86952f2ecb308947071d34ecf/src/engine/SCons/Tool/MSCommon/?at=2.3.6
    #
    result = {}
    try:
        _VCVER_TO_PRODUCT_DIR = {
                '14.0' : [
                    r'Microsoft\VisualStudio\14.0\Setup\VC\ProductDir'],
                '12.0' : [
                    r'Microsoft\VisualStudio\12.0\Setup\VC\ProductDir'],
                '12.0Exp' : [
                    r'Microsoft\VCExpress\12.0\Setup\VC\ProductDir'],
                '11.0': [
                    r'Microsoft\VisualStudio\11.0\Setup\VC\ProductDir'],
                '11.0Exp' : [
                    r'Microsoft\VCExpress\11.0\Setup\VC\ProductDir'],
                '10.0': [
                    r'Microsoft\VisualStudio\10.0\Setup\VC\ProductDir'],
                '10.0Exp' : [
                    r'Microsoft\VCExpress\10.0\Setup\VC\ProductDir'],
                '9.0': [
                    r'Microsoft\VisualStudio\9.0\Setup\VC\ProductDir'],
                '9.0Exp' : [
                    r'Microsoft\VCExpress\9.0\Setup\VC\ProductDir'],
                '8.0': [
                    r'Microsoft\VisualStudio\8.0\Setup\VC\ProductDir'],
                '8.0Exp': [
                    r'Microsoft\VCExpress\8.0\Setup\VC\ProductDir'],
                '7.1': [
                    r'Microsoft\VisualStudio\7.1\Setup\VC\ProductDir'],
                '7.0': [
                    r'Microsoft\VisualStudio\7.0\Setup\VC\ProductDir'],
                '6.0': [
                    r'Microsoft\VisualStudio\6.0\Setup\Microsoft Visual C++\ProductDir']
        }
        # attempt to load the windows registry module:
        can_read_reg = 0
        try:
            import winreg

            can_read_reg = 1
            hkey_mod = winreg

            RegOpenKeyEx    = winreg.OpenKeyEx
            RegQueryValueEx = winreg.QueryValueEx

        except ImportError:
            try:
                import win32api
                import win32con
                can_read_reg = 1
                hkey_mod = win32con

                RegOpenKeyEx    = win32api.RegOpenKeyEx
                RegQueryValueEx = win32api.RegQueryValueEx

            except ImportError:
                pass

        if can_read_reg:
            def RegGetValue(root, key):
                p = key.rfind('\\') + 1
                keyp = key[:p-1]          # -1 to omit trailing slash
                val = key[p:]
                k = RegOpenKeyEx(root, keyp)
                return RegQueryValueEx(k,val)

            root = 'Software\\'
            if is_win64:
                root = root + 'Wow6432Node\\'
            for version, key in _VCVER_TO_PRODUCT_DIR.iteritems():
                key = root + key[0]
                try:
                    comps = RegGetValue(hkey_mod.HKEY_LOCAL_MACHINE, key)
                    result[version] = comps[0]
                except:
                    pass

    finally:
        return result

setupmsvc = """
call "{msvc_root}\\vcvarsall.bat" %1
set
"""

def commands():
    import subprocess
    import os

    env.PATH.append('{this.root}/bin')
    arch_argument = "x86_amd64" if env.REZ_TARGET_VERSION == "AMD64" else "x86"

    cmd="%s\\bin\\setupmsvc.bat %s" % (this.root, arch_argument)
    p = subprocess.Popen(cmd, shell=True,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

    stdout, _ = p.communicate()

    for line in stdout.splitlines():
        kv = line.split('=')
        if len(kv) == 2:
            key = kv[0]
            value = kv[1]
            values = value.split(os.pathsep)
            if len(values) == 1 or key not in os.environ:
                setenv(key, value)
            else:
                osv = os.environ.get(key).split(os.pathsep)
                for v in values:
                    if v in osv:continue
                    appendenv(key, v)


def setup_parser(parser):
    parser.add_argument("--version", type=str, metavar="VERSION",
                        help="manually specify the msvc version to install.")
    parser.add_argument("--root", type=str, metavar="ROOT",
                        help="the root of msvc where to find vcvarsall.bat.")

def bind(path, version_range=None, opts=None, parser=None):
    is_win64 = False
    if os.environ.get('PROCESSOR_ARCHITECTURE','x86') != 'x86':
        is_win64 = True
    if os.environ.get('PROCESSOR_ARCHITEW6432'):
        is_win64 = True
    if os.environ.get('ProgramW6432'):
        is_win64 = True

    msvcs = enumerate_msvc(is_win64)
    if not msvcs:
        version = getattr(opts, "version", None)
        if not version:
            _msvc_root = getattr(opts, "root", None)
            if not _msvc_root:
                print "can't find msvc root, use --root and pass the directory to find vcvarsall.bat"
                exit(1)
            else:
                vcvarsall = os.path.join(_msvc_root, "vcvarsall.bat")
                if not os.path.isfile(vcvarsall):
                    print "can't find:", vcvarsall
                    exit(1)
                version = os.path.split(_msvc_root)[-2].split()[-1]
                msvcs[version] = _msvc_root

    for variant in ['target-AMD64', 'target-AMD86']:
        variants = system.variant+[variant]
        for version, msvc_root in msvcs.iteritems():
            check_version(version, version_range)

            def make_root(variant, root):
                binpath = make_dirs(root, "bin")
                bat = os.path.join(binpath, "setupmsvc.bat")
                with open(bat, "w") as f:
                    f.write(setupmsvc.format(msvc_root=msvc_root))

            with make_package("msvc", path, make_root=make_root) as pkg:
                pkg.version = version
                pkg.tools = ["setupmsvc"]
                pkg.commands = commands
                pkg.variants = [variants]

    return "msvc", msvcs.keys()
