from pathlib import Path

import pytest

from mcph.extensions import (
    BuiltinResolver,
    DirectoryResolver,
    PackError,
    ResolvedPack,
    resolve_pack,
)


@pytest.fixture
def base(tmp_path):
    """The directory of a mcph.yaml with a packs directory next to it."""
    (tmp_path / "project" / "packs" / "acme").mkdir(parents=True)
    return tmp_path / "project"


# --- a local directory ------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["./packs/acme", "packs/acme", "packs/../packs/acme/"])
def test_a_relative_directory_is_taken_from_the_directory_of_the_config(base, source):
    resolved = DirectoryResolver().resolve(source, base)

    assert resolved == ResolvedPack(source, base / "packs" / "acme")


def test_the_directory_is_relative_to_the_config_and_not_to_the_working_directory(
    base, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    assert DirectoryResolver().resolve("./packs/acme", base).directory == base / "packs" / "acme"


def test_an_absolute_directory_is_used_as_it_is(base, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    resolved = DirectoryResolver().resolve(str(elsewhere), base)

    assert resolved.directory == elsewhere


def test_a_home_directory_is_expanded(base, tmp_path, monkeypatch):
    (tmp_path / "home" / "packs" / "mine").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = DirectoryResolver().resolve("~/packs/mine", base)

    assert resolved.directory == tmp_path / "home" / "packs" / "mine"


def test_the_resolved_directory_is_absolute_even_for_a_relative_base(
    base, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    resolved = DirectoryResolver().resolve("./packs/acme", Path("project"))

    assert resolved.directory.is_absolute()
    assert resolved.directory == base / "packs" / "acme"


def test_a_directory_that_does_not_exist_is_reported_with_its_path_and_source(base):
    with pytest.raises(PackError) as excinfo:
        DirectoryResolver().resolve("./packs/nope", base)

    assert str(excinfo.value) == (
        f"pack directory not found: {base / 'packs' / 'nope'} (from './packs/nope')"
    )


def test_a_file_is_not_a_pack_directory(base):
    (base / "packs" / "file.txt").write_text("x")

    with pytest.raises(PackError, match="'./packs/file.txt' is not a directory"):
        DirectoryResolver().resolve("./packs/file.txt", base)


@pytest.mark.parametrize(
    "source", ["builtin:core", "git+https://example.com/p.git@v1", "https://example.com/p"]
)
def test_the_directory_resolver_leaves_sources_with_a_scheme_to_others(base, source):
    assert DirectoryResolver().resolve(source, base) is None


# --- a pack bundled with mcph -----------------------------------------------------------------


@pytest.fixture
def bundled(tmp_path):
    root = tmp_path / "bundled"
    for name in ("core", "cursor"):
        (root / name).mkdir(parents=True)
        (root / name / "mcph-pack.yaml").write_text(f'name: {name}\nversion: "1"\n')
    (root / "not-a-pack").mkdir()  # no manifest
    return root


def test_a_builtin_pack_is_found_in_the_bundled_directory(bundled, base):
    resolved = BuiltinResolver(bundled).resolve("builtin:core", base)

    assert resolved == ResolvedPack("builtin:core", bundled / "core")


def test_an_unknown_builtin_pack_lists_the_available_ones(bundled, base):
    with pytest.raises(PackError) as excinfo:
        BuiltinResolver(bundled).resolve("builtin:nope", base)

    assert str(excinfo.value) == (
        "no built-in pack named 'nope' in 'builtin:nope' (available: core, cursor)"
    )


@pytest.mark.parametrize("source", ["builtin:", "builtin:a/b", "builtin:../x", "builtin:not-a-pack"])
def test_a_builtin_name_must_be_the_name_of_a_bundled_pack(bundled, base, source):
    with pytest.raises(PackError, match="no built-in pack named"):
        BuiltinResolver(bundled).resolve(source, base)


def test_when_nothing_is_bundled_there_is_nothing_available(tmp_path, base):
    resolver = BuiltinResolver(tmp_path / "missing")

    assert resolver.available() == []
    with pytest.raises(PackError, match=r"\(available: none\)"):
        resolver.resolve("builtin:core", base)


def test_the_builtin_resolver_leaves_other_sources_to_others(bundled, base):
    assert BuiltinResolver(bundled).resolve("./packs/acme", base) is None


def test_only_directories_with_a_manifest_are_available(bundled):
    assert BuiltinResolver(bundled).available() == ["core", "cursor"]


# --- choosing a resolver ----------------------------------------------------------------------


def test_the_default_resolvers_handle_directories(base):
    resolved = resolve_pack("./packs/acme", base)

    assert resolved.directory == base / "packs" / "acme"
    assert resolved.source == "./packs/acme"


def test_a_source_that_no_resolver_understands_is_unsupported(base):
    with pytest.raises(PackError) as excinfo:
        resolve_pack("git+https://example.com/p.git@v1", base)

    assert str(excinfo.value) == (
        "unsupported pack source 'git+https://example.com/p.git@v1': "
        "use a directory or 'builtin:<name>'"
    )


@pytest.mark.parametrize("source", ["", "   "])
def test_an_empty_source_is_an_error(base, source):
    with pytest.raises(PackError, match="must not be empty"):
        resolve_pack(source, base)


def test_the_first_resolver_that_is_responsible_decides(base, bundled):
    class Fixed:
        def resolve(self, source, base_dir):
            return ResolvedPack(source, Path("/fixed"))

    class Never:
        def resolve(self, source, base_dir):
            return None

    assert resolve_pack("anything", base, [Never(), Fixed(), DirectoryResolver()]).directory == Path(
        "/fixed"
    )
    assert resolve_pack("./packs/acme", base, [Fixed(), DirectoryResolver()]).directory == Path(
        "/fixed"
    )


def test_without_resolvers_nothing_is_supported(base):
    with pytest.raises(PackError, match="unsupported pack source"):
        resolve_pack("./packs/acme", base, [])


def test_a_resolver_that_fails_ends_the_search(base, bundled):
    resolvers = [BuiltinResolver(bundled), DirectoryResolver()]

    with pytest.raises(PackError, match="no built-in pack named 'nope'"):
        resolve_pack("builtin:nope", base, resolvers)
