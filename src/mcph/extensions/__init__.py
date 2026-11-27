"""Extensions: Harness Packs that add rules to mcph.

A pack is a directory with a ``mcph-pack.yaml`` manifest. ``pack`` reads the
manifest, ``resolver`` finds the directory a source stands for, ``registry``
loads the packs and collects their rules, and ``loading`` loads the packs a
config names. Without any pack there is no rule: even the rules that ship with
mcph are the bundled ``core`` pack. Pack authors need only :func:`doctor_rule`.
"""

from mcph.extensions.pack import MANIFEST_NAME, PackError, PackManifest, load_manifest
from mcph.extensions.loading import ADD_A_PACK, load_extensions
from mcph.extensions.registry import (
    LoadedPack,
    Registry,
    doctor_rule,
    load_pack,
    load_registry,
)
from mcph.extensions.resolver import (
    BuiltinResolver,
    DirectoryResolver,
    ResolvedPack,
    Resolver,
    resolve_pack,
)

__all__ = [
    "ADD_A_PACK",
    "MANIFEST_NAME",
    "BuiltinResolver",
    "DirectoryResolver",
    "LoadedPack",
    "PackError",
    "PackManifest",
    "Registry",
    "ResolvedPack",
    "Resolver",
    "doctor_rule",
    "load_extensions",
    "load_manifest",
    "load_pack",
    "load_registry",
    "resolve_pack",
]
