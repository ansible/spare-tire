from __future__ import annotations

import boto3
import json
import re
import typing as t
import yaml

from dataclasses import dataclass
from packaging.version import Version
from packaging.specifiers import SpecifierSet
from qypi.api import QyPI


@dataclass(frozen=True)
class BuildSpec:
    package: str
    version: str
    platform_instance: str
    platform_arch: str
    python_tag: str
    abi_tag: t.Optional[str]
    platform_tag: str
    sdist_url: str
    constraints: str

    @property
    def sdist_dir(self) -> str:
        return '-'.join((self.package.replace('-', '_'), str(self.version)))

    @property
    def filename(self) -> str:
        return '-'.join([c for c in (self.sdist_dir, self.python_tag, self.abi_tag or self.python_tag, self.platform_tag) if c]) + '.whl'


class PackageBuildChecker:
    def __init__(self, pkg_matrix: dict):
        self._pkg_matrix = pkg_matrix
        self._missing_build_spec = dict()

    def _find_missing(self) -> set[BuildSpec]:
        pypi = QyPI('https://pypi.org/pypi')
        missing: set[BuildSpec] = set()
        for pkg_name, pkg_reqs in self._pkg_matrix['packages'].items():
            for pkg_ver, pkg_builds in pkg_reqs['versions'].items():
                if pkg_ver == 'latest':
                    published_pkg = pypi.get_latest_version(pkg_name)
                else:
                    published_pkg = pypi.get_version(pkg_name, pkg_ver)
                version = published_pkg['info']['version']
                for pkg_build in pkg_builds['wheels']:
                    platform_tag = pkg_build['platform_tag']
                    platform_instance = pkg_build['platform_instance']
                    platform_arch = pkg_build['platform_arch']
                    for python_spec in pkg_build['python']:
                        python_tag = python_spec['tag']
                        abi_tag = python_spec.get('abi', '')
                        sdist_url = next(r for r in published_pkg['urls'] if r.get('packagetype') == 'sdist')['url']
                        constraints = generate_constraints(pkg_name, version)
                        spec = BuildSpec(pkg_name, version, platform_instance, platform_arch, python_tag, abi_tag, platform_tag, sdist_url, constraints)
                        if not self._build_exists(spec):
                            missing.add(spec)
        return missing

    @staticmethod
    def _build_exists(spec: BuildSpec) -> bool:
        print(f"checking bucket for {spec.filename}")
        s3 = boto3.client('s3')
        s3_objects = s3.list_objects_v2(Bucket='spare-tire', Prefix=f'packages/{spec.filename}', MaxKeys=1)
        exists = len(s3_objects.get('Contents', [])) > 0
        if not exists:
            print(f"{spec.filename} is not present in bucket")
        return exists

    @staticmethod
    def _pytag_to_python_version(tag):
        m = re.match(r'cp(?P<maj>\d)(?P<min>\d{1,2})$', tag)
        if not m:
            raise KeyError(f'invalid python tag {tag}')
        return f'{m.group("maj")}.{m.group("min")}'

    @staticmethod
    def _pytag_to_python(tag):
        return f'python{PackageBuildChecker._pytag_to_python_version(tag)}'

    @property
    def build_matrix(self) -> dict:
        missing = self._find_missing()
        matrix = dict()
        # output matrix should be:
        # wheel_freebsd/12.2:
        #   job_data:
        #     instance: freebsd/12.2
        #     packages:
        #     - name: cryptography
        #       version: 36.0.1
        #       python: cp38
        #       abi: abi3
        # wheel_freebsd/13.0:
        #   job_data:
        #     instance: freebsd/13.0
        #     packages:
        #     - name: cryptography
        #       version: 36.0.1
        #       python: cp38
        #       abi: abi3

        for missing_build in missing:
            job_name = f'wheel_{missing_build.platform_tag}'
            job_toplevel = matrix.setdefault(job_name, {})
            job_toplevel['instance'] = missing_build.platform_instance
            job_toplevel['arch'] = missing_build.platform_arch
            job_def = job_toplevel.setdefault('job_data', {})
            job_def['instance'] = missing_build.platform_instance
            job_def['arch'] = missing_build.platform_arch
            pkgs = job_def.setdefault('packages', [])
            pkgs.append(dict(
                name=missing_build.package,
                version=missing_build.version,
                python=self._pytag_to_python(missing_build.python_tag),
                python_version=self._pytag_to_python_version(missing_build.python_tag),
                python_tag=missing_build.python_tag,
                abi=missing_build.abi_tag,
                sdist_dir=missing_build.sdist_dir,
                sdist_url=missing_build.sdist_url,
                expected_output_filename=missing_build.filename,
                constraints=missing_build.constraints,
            ))

        # HACK: azp barfs on > 2 levels of nesting, and only allows string values, so we have to smuggle JSON in a
        # string key for the actual structured job data
        for job_name, job in matrix.items():
            python_versions = list(sorted(set(str_to_version(package['python_version']) for package in job['job_data']['packages'])))
            python_version = python_versions.pop(0)

            job['python'] = version_to_str(python_version)  # python version used to request the instance
            job['pythons'] = ' '.join(version_to_str(v) for v in python_versions)  # additional python versions to install, if any

            job['job_data'] = json.dumps(job['job_data'])
            print(f'{job_name} data is {job["job_data"]}')

        matrix = dict(sorted(matrix.items()))

        print(f'output matrix is now: {matrix}')

        return matrix


def main():
    with open('wheel_matrix.yml', 'rb') as fd:
        pkg_matrix = yaml.safe_load(fd)

    pbc = PackageBuildChecker(pkg_matrix)
    build_matrix = pbc.build_matrix
    print('dumping build matrix to variable `matrix`')
    print(f'##vso[task.setvariable variable=matrix;isOutput=true]{json.dumps(build_matrix)}')
    if build_matrix:
        # HACK: can't figure out a stage expression that can directly sample an empty matrix to skip the subsequent stages, so we need this extra var
        print(f'##vso[task.setvariable variable=matrix_has_jobs;isOutput=true]true')


def str_to_version(value: str) -> tuple[int, ...]:
    return tuple(int(v) for v in value.split('.'))


def version_to_str(value: tuple[int, ...]) -> str:
    return '.'.join(str(v) for v in value)


def generate_constraints(pkg_name: str, version: str) -> str:
    build_constraints = (
        ('pyyaml', '>= 5.4, <= 6.0', ('Cython < 3.0',)),
    )

    pkg_name = pkg_name.lower()
    pkg_version = Version(version)

    for package, specifier, constraints in build_constraints:
        if package == pkg_name and SpecifierSet(specifier).contains(pkg_version):
            return '\n'.join(constraints)

    return ''


if __name__ == '__main__':
    main()
