# SPDX-License-Identifier: LGPL-3.0-only
"""Read, rebuild and extract LEGO Island SI files."""

from . import si
from .assets import extract
from .weave import Project, unweave, weave

__all__ = ["Project", "decompile", "rebuild", "extract"]


def decompile(path):
    """SI path → editable source, action IDs, raw media and residue."""
    return unweave(si.parse(path))


def rebuild(project):
    """Project or project-directory path → SI bytes."""
    if not isinstance(project, Project):
        project = Project.read(project)
    return weave(project)
