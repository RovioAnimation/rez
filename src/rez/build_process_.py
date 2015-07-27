from rez.packages_ import get_developer_package, iter_packages
from rez.exceptions import BuildProcessError, BuildContextResolveError, \
    ReleaseHookCancellingError, RezError, ReleaseError, BuildError, \
    ReleaseVCSError
from rez.utils.logging_ import print_warning
from rez.resolved_context import ResolvedContext
from rez.release_hook import create_release_hooks
from rez.resolver import ResolverStatus
from rez.config import config
from rez.vendor.enum import Enum
from contextlib import contextmanager
import getpass
import os.path


def get_build_process_types():
    """Returns the available build process implementations."""
    from rez.plugin_managers import plugin_manager
    return plugin_manager.get_plugins('build_process')


def create_build_process(process_type, working_dir, build_system, vcs=None,
                         ensure_latest=True, skip_errors=False,
                         ignore_existing_tag=False, verbose=False):
    """Create a `BuildProcess` instance."""
    from rez.plugin_managers import plugin_manager
    process_types = get_build_process_types()
    if process_type not in process_type:
        raise BuildProcessError("Unknown build process: %r" % process_type)
    cls = plugin_manager.get_plugin_class('build_process', process_type)

    return cls(working_dir,
               build_system=build_system,
               vcs=vcs,
               ensure_latest=ensure_latest,
               skip_errors=skip_errors,
               ignore_existing_tag=ignore_existing_tag,
               verbose=verbose)


class BuildType(Enum):
    """ Enum to represent the type of build."""
    local = 0
    central = 1


class BuildProcess(object):
    """A BuildProcess builds and possibly releases a package.

    A build process iterates over the variants of a package, creates the
    correct build environment for each variant, builds that variant using a
    build system (or possibly creates a script so the user can do that
    independently), and then possibly releases the package with the nominated
    VCS. This is an abstract base class, you should use a BuildProcess
    subclass.
    """
    @classmethod
    def name(cls):
        raise NotImplementedError

    def __init__(self, working_dir, build_system, vcs=None, ensure_latest=True,
                 skip_errors=False, ignore_existing_tag=False, verbose=False):
        """Create a BuildProcess.

        Args:
            working_dir (str): Directory containing the package to build.
            build_system (`BuildSystem`): Build system used to build the package.
            vcs (`ReleaseVCS`): Version control system to use for the release
                process. If None, the package will only be built, not released.
            ensure_latest: If True, do not allow the release process to occur
                if an newer versioned package is already released.
            skip_errors: If True, proceed with the release even when errors
                occur. BE CAREFUL using this option, it is here in case a package
                needs to be released urgently even though there is some problem
                with reading or writing the repository.
            ignore_existing_tag: Perform the release even if the repository is
                already tagged at the current version. If the config setting
                plugins.release_vcs.check_tag is False, this has no effect.
        """
        self.verbose = verbose
        self.working_dir = working_dir
        self.build_system = build_system
        self.vcs = vcs
        self.ensure_latest = ensure_latest
        self.skip_errors = skip_errors
        self.ignore_existing_tag = ignore_existing_tag

        if vcs and vcs.path != working_dir:
            raise BuildProcessError(
                "Build process was instantiated with a mismatched VCS instance")

        self.debug_print = config.debug_printer("package_release")
        self.package = get_developer_package(working_dir)
        hook_names = self.package.config.release_hooks or []
        self.hooks = create_release_hooks(hook_names, working_dir)
        self.build_path = os.path.join(self.working_dir,
                                       self.package.config.build_directory)

    def build(self, install_path=None, clean=False, install=False, variants=None):
        """Perform the build process.

        Iterates over the package's variants, resolves the environment for
        each, and runs the build system within each resolved environment.

        Args:
            install_path (str): The package repository path to install the
                package to, if installing. If None, defaults to
                `config.local_packages_path`.
            clean (bool): If True, clear any previous build first. Otherwise,
                rebuild over the top of a previous build.
            install (bool): If True, install the build.
            variants (list of int): Indexes of variants to build, all if None.

        Raises:
            `BuildError`: If the build failed.

        Returns:
            int: Number of variants successfully built.
        """
        raise NotImplementedError

    def release(self, release_message=None, variants=None):
        """Perform the release process.

        Iterates over the package's variants, building and installing each into
        the release path determined by `config.release_packages_path`.

        Args:
            release_message (str): Message to associate with the release.
            variants (list of int): Indexes of variants to release, all if None.

        Raises:
            `ReleaseError`: If the release failed.

        Returns:
            int: Number of variants successfully released.
        """
        raise NotImplementedError


class BuildProcessHelper(BuildProcess):
    """A BuildProcess base class with some useful functionality.
    """
    @contextmanager
    def repo_operation(self):
        exc_type = ReleaseVCSError if self.skip_errors else None
        try:
            yield
        except exc_type as e:
            print_warning("THE FOLLOWING ERROR WAS SKIPPED:\n%s" % str(e))

    def visit_variants(self, func, variants=None, **kwargs):
        """Iterate over variants and call a function on each."""
        if variants:
            present_variants = range(self.package.num_variants)
            invalid_variants = set(variants) - set(present_variants)
            if invalid_variants:
                raise BuildError(
                    "The package does not contain the variants: %s"
                    % ", ".join(str(x) for x in sorted(invalid_variants)))

        # iterate over variants
        results = []
        num_visited = 0

        for variant in self.package.iter_variants():
            if variants and variant.index not in variants:
                self._print_header("Skipping %s..." % self._n_of_m(variant))
                continue

            result = func(variant, **kwargs)
            results.append(result)
            num_visited += 1

        return num_visited, results

    def get_package_install_path(self, path):
        """Return the installation path for a package (where its payload goes).
        """
        path_ = os.path.join(path, self.package.name)
        if self.package.version:
            path_ = os.path.join(path_, str(self.package.version))
        return path_

    def create_build_context(self, variant, build_type, build_path):
        """Create a context to build the variant within."""
        request = variant.get_requires(build_requires=True,
                                       private_build_requires=True)

        requests_str = ' '.join(map(str, request))
        self._print("Resolving build environment: %s", requests_str)
        if build_type == BuildType.local:
            packages_path = self.package.config.packages_path
        else:
            packages_path = self.package.config.nonlocal_packages_path

        context = ResolvedContext(request,
                                  package_paths=packages_path,
                                  building=True)
        if self.verbose:
            context.print_info()

        # save context before possible fail, so user can debug
        rxt_filepath = os.path.join(build_path, "build.rxt")
        context.save(rxt_filepath)

        if context.status != ResolverStatus.solved:
            raise BuildContextResolveError(context)
        return context, rxt_filepath

    def pre_release(self):
        release_settings = self.package.config.plugins.release_vcs

        # test that the release path exists
        release_path = self.package.config.release_packages_path
        if not os.path.exists(release_path):
            raise ReleaseError("Release path does not exist: %r" % release_path)

        # test that the repo is in a state to release
        assert self.vcs
        self._print("Checking state of repository...")
        with self.repo_operation():
            self.vcs.validate_repostate()

        # check if the repo is already tagged at the current version
        if release_settings.check_tag and not self.ignore_existing_tag:
            tag_name = self.get_current_tag_name()
            tag_exists = False
            with self.repo_operation():
                tag_exists = self.vcs.tag_exists(tag_name)

            if tag_exists:
                raise ReleaseError(
                    "Cannot release - the current package version '%s' is already "
                    "tagged in the repository. Use --ignore-existing-tag to "
                    "force the release" % self.package.version)

        it = iter_packages(self.package.name, paths=[release_path])
        packages = sorted(it, key=lambda x: x.version, reverse=True)

        # check UUID. This stops unrelated packages that happen to have the same
        # name, being released as though they are the same package
        if self.package.uuid and packages:
            latest_package = packages[0]
            if latest_package.uuid and latest_package.uuid != self.package.uuid:
                raise ReleaseError(
                    "Cannot release - the packages are not the same (UUID mismatch)")

        # test that a newer package version hasn't already been released
        if self.ensure_latest:
            for package in packages:
                if package.version > self.package.version:
                    raise ReleaseError(
                        "Cannot release - a newer package version already "
                        "exists (%s)" % package.uri)
                else:
                    break

    def post_release(self, release_message=None):
        tag_name = self.get_current_tag_name()

        # write a tag for the new release into the vcs
        assert self.vcs
        with self.repo_operation():
            self.vcs.create_release_tag(tag_name=tag_name, message=release_message)

    def get_current_tag_name(self):
        release_settings = self.package.config.plugins.release_vcs
        try:
            tag_name = self.package.format(release_settings.tag_name)
        except Exception as e:
            raise ReleaseError("Error formatting release tag name: %s" % str(e))
        if not tag_name:
            tag_name = "unversioned"
        return tag_name

    def run_hooks(self, hook_event, **kwargs):
        for hook in self.hooks:
            self.debug_print("Running %s hook '%s'...",
                             hook_event.label, hook.name())
            try:
                func = getattr(hook, hook_event.func_name)
                func(user=getpass.getuser(), **kwargs)
            except ReleaseHookCancellingError as e:
                raise ReleaseError(
                    "%s cancelled by %s hook '%s': %s:\n%s"
                    % (hook_event.noun, hook_event.label, hook.name(),
                       e.__class__.__name__, str(e)))
            except RezError:
                self.debug_print(
                    "Error in %s hook '%s': %s:\n%s"
                    % (hook_event.label, hook.name(),
                       e.__class__.__name__, str(e)))

    def get_previous_release(self):
        release_path = self.package.config.release_packages_path
        it = iter_packages(self.package.name, paths=[release_path])
        packages = sorted(it, key=lambda x: x.version, reverse=True)

        for package in packages:
            if package.version < self.package.version:
                return package
        return None

    def get_release_data(self):
        """Get release data for this release.

        Returns:
            dict.
        """
        previous_package = self.get_previous_release()
        if previous_package:
            previous_version = previous_package.version
            previous_revision = previous_package.revision
        else:
            previous_version = None
            previous_revision = None

        assert self.vcs
        revision = None
        changelog = None

        with self.repo_operation():
            revision = self.vcs.get_current_revision()
        with self.repo_operation():
            changelog = self.vcs.get_changelog(previous_revision)

        # truncate changelog - very large changelogs can cause package load
        # times to be very high, we don't want that
        maxlen = config.max_package_changelog_chars
        if maxlen and changelog and len(changelog) > maxlen + 3:
            changelog = changelog[:maxlen] + "..."

        return dict(vcs=self.vcs.name(),
                    revision=revision,
                    changelog=changelog,
                    previous_version=previous_version,
                    previous_revision=previous_revision)

    def _print(self, txt, *nargs):
        if self.verbose:
            if nargs:
                txt = txt % nargs
            print txt

    def _print_header(self, txt, n=1):
        self._print('')
        if n <= 1:
            self._print('-' * 80)
            self._print(txt)
            self._print('-' * 80)
        else:
            self._print(txt)
            self._print('-' * len(txt))

    def _n_of_m(self, variant):
        num_variants = max(self.package.num_variants, 1)
        index = (variant.index or 0) + 1
        return "%d/%d" % (index, num_variants)
