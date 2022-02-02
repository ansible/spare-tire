import json
import re
import typing as t
import yaml

from dataclasses import dataclass
from qypi.api import QyPI

@dataclass(frozen=True)
class BuildSpec:
    package: str
    version: str
    platform_instance: str
    python_tag: str
    abi_tag: t.Optional[str]
    platform_tag: str

    @property
    def filename(self):
        base_filename = '-'.join([c for c in (self.package, str(self.version), self.python_tag, self.abi_tag, self.platform_tag) if c])
        return f'{base_filename}.whl'


class PackageBuildChecker:
    def __init__(self, pkg_matrix: dict):
        self._pkg_matrix = pkg_matrix
        self._missing_build_spec = dict()
        self._find_missing()

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
                    for python_spec in pkg_build['python']:
                        python_tag = python_spec['tag']
                        abi_tag = python_spec.get('abi', '')
                        spec = BuildSpec(pkg_name, version, platform_instance, python_tag, abi_tag, platform_tag)
                        if not self._build_exists(spec):
                            missing.add(spec)
        return missing

    def _build_exists(self, spec: BuildSpec) -> bool:
        print(f'would check for {spec.filename}')
        return False

    @staticmethod
    def _pytag_to_python(tag):
        m = re.match(r'cp(?P<maj>\d)(?P<min>\d{1,2})$', tag)
        if not m:
            raise KeyError(f'invalid python tag {tag}')
        return f'python{m.group("maj")}.{m.group("min")}'

    @property
    def build_matrix(self) -> dict:
        missing = self._find_missing()
        matrix = dict()
        # output matrix should be:
        # wheel_freebsd/12.2:
        #   instance: freebsd/12.2
        #   packages:
        #     - name: cryptography
        #       version: 36.0.1
        #       python: cp38
        #       abi: abi3
        # wheel_freebsd/13.0:
        #   instance: freebsd/13.0
        #   packages:
        #     - name: cryptography
        #       version: 36.0.1
        #       python: cp38
        #       abi: abi3

        for missing_build in missing:
            job_name = f'wheel_{missing_build.platform_tag}'
            job_def = matrix.setdefault(job_name, {})
            job_def['instance'] = missing_build.platform_instance
            # pkgs = job_def.setdefault('packages', [])
            # pkgs.append(dict(
            #     name=missing_build.package,
            #     version=missing_build.version,
            #     python=self._pytag_to_python(missing_build.python_tag),
            #     abi=missing_build.abi_tag
            # ))

        return matrix


def main():
    with open('wheel_matrix.yml', 'rb') as fd:
        pkg_matrix = yaml.safe_load(fd)

    pbc = PackageBuildChecker(pkg_matrix)
    build_matrix = pbc.build_matrix
    print('dumping top-level matrix to variable `matrix`')
    jobs = {job: dict(instance=job_def['instance']) for job, job_def in sorted(build_matrix.items())}
    print(f'##vso[task.setvariable variable=matrix;isOutput=true]{json.dumps(jobs)}')
    for job, job_def in build_matrix.items():
        print(f'##vso[task.setvariable variable={job};isOutput=true]{json.dumps(job_def)}')


if __name__ == '__main__':
    main()