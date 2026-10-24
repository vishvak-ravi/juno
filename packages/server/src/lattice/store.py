"""Where lattices are kept: one directory per build.

``Lattices`` is what the server and the build script read and write through;
``FileLattices`` keeps build ``<id>`` under ``<lattice dir>/<id>/``: its record
in ``build.json`` from the moment it starts, and the lattice in ``lattice.json``
once there is one. The lattice directory is ``<data dir>/lattice``.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional, Protocol

from pydantic import ValidationError

from juno.sessions import replace_file
from lattice.models import Lattice, LatticeBuild

logger = logging.getLogger(__name__)

LATTICE_DIR = "lattice"
BUILD_FILE = "build.json"
LATTICE_FILE = "lattice.json"
BUILD_ID_PATTERN = r"lattice_\d{8}_\d{6}"
BUILD_ID = re.compile(f"^{BUILD_ID_PATTERN}$")


class Lattices(Protocol):

    def get_build(self, build_id: str) -> Optional[LatticeBuild]:
        """The build's record, or None if there is none."""

    def save_build(self, build: LatticeBuild) -> None:
        """Store the record, replacing the build's earlier one."""

    def get(self, build_id: str) -> Optional[Lattice]:
        """The build's lattice, or None if the build has not finished one."""

    def save(self, lattice: Lattice) -> None:
        """Store the lattice and its record."""

    def builds(self) -> list[LatticeBuild]:
        """Every build's record, newest first."""


def new_build_id(now: Optional[float] = None) -> str:
    return time.strftime("lattice_%Y%m%d_%H%M%S", time.localtime(time.time() if now is None else now))


def lattice_dir(data_dir: Path) -> Path:
    return data_dir / LATTICE_DIR


class FileLattices:

    def __init__(self, directory: Path):
        self.directory = directory

    def build_dir(self, build_id: str) -> Path:
        if not BUILD_ID.match(build_id):
            raise ValueError(f"not a build id: {build_id!r}")
        return self.directory / build_id

    def get_build(self, build_id: str) -> Optional[LatticeBuild]:
        try:
            return LatticeBuild.model_validate_json((self.build_dir(build_id) / BUILD_FILE).read_text())
        except FileNotFoundError:
            return None

    def save_build(self, build: LatticeBuild) -> None:
        replace_file(self.build_dir(build.id) / BUILD_FILE, build.model_dump_json(indent=2))

    def get(self, build_id: str) -> Optional[Lattice]:
        try:
            return Lattice.model_validate_json((self.build_dir(build_id) / LATTICE_FILE).read_text())
        except FileNotFoundError:
            return None

    def save(self, lattice: Lattice) -> None:
        self.save_build(lattice.build)
        replace_file(self.build_dir(lattice.build.id) / LATTICE_FILE, lattice.model_dump_json())

    def builds(self) -> list[LatticeBuild]:
        builds = []
        if not self.directory.is_dir():
            return builds
        for path in self.directory.glob(f"*/{BUILD_FILE}"):
            try:
                builds.append(LatticeBuild.model_validate_json(path.read_text()))
            except (OSError, ValidationError) as exc:
                logger.warning("skipping %s: %s", path, exc)
        return sorted(builds, key=lambda b: b.created_at, reverse=True)
