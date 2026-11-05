"""Bundle the authoritative config/ YAML files when building a wheel."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithDefaultConfig(build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / "etf_temperature" / "_default_config"
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted((Path(__file__).parent / "config").glob("*.yaml")):
            self.copy_file(str(source), str(destination / source.name))


setup(cmdclass={"build_py": BuildWithDefaultConfig})
