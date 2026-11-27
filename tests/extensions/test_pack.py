import pytest

from mcph.extensions import PackError, PackManifest, load_manifest


def manifest_error(directory, text: str) -> str:
    (directory / "mcph-pack.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(PackError) as excinfo:
        load_manifest(directory)
    return str(excinfo.value)


@pytest.fixture
def pack(tmp_path):
    directory = tmp_path / "acme"
    directory.mkdir()
    return directory


def test_a_minimal_manifest_applies_defaults(pack):
    (pack / "mcph-pack.yaml").write_text('name: acme\nversion: "1.0.0"\n')

    assert load_manifest(pack) == PackManifest(name="acme", version="1.0.0")
    assert load_manifest(pack).description == "" and load_manifest(pack).rules == []


def test_a_full_manifest(pack):
    (pack / "mcph-pack.yaml").write_text(
        "name: claude-code\nversion: 2.1.0\ndescription: Checks for a client.\n"
        "rules:\n  - rules/schemas.py\n  - ./rules/names.py\n"
    )

    manifest = load_manifest(pack)

    assert manifest.name == "claude-code"
    assert manifest.version == "2.1.0"  # YAML reads 2.1.0 as a string
    assert manifest.description == "Checks for a client."
    assert manifest.rules == ["rules/schemas.py", "./rules/names.py"]


def test_a_pack_without_a_manifest_is_not_a_pack(pack):
    with pytest.raises(PackError) as excinfo:
        load_manifest(pack)

    assert str(excinfo.value) == f"{pack} is not a pack: it has no mcph-pack.yaml"


def test_a_manifest_that_cannot_be_read_is_reported(pack):
    (pack / "mcph-pack.yaml").mkdir()  # a directory where the file should be

    with pytest.raises(PackError, match="cannot read .*mcph-pack.yaml"):
        load_manifest(pack)


def test_invalid_yaml_is_reported_with_the_position(pack):
    message = manifest_error(pack, "name: [unclosed\nversion: 1\n")

    assert message.startswith(f"invalid YAML in {pack / 'mcph-pack.yaml'}:")
    assert "line" in message


def test_invalid_yaml_without_a_position_is_reported_too(pack):
    message = manifest_error(pack, "name: acme\x07\n")  # a control character

    assert message.startswith("invalid YAML in ")
    assert "special characters are not allowed" in message
    assert "(line" not in message


@pytest.mark.parametrize(
    ("text", "described"),
    [("", "an empty file"), ("- a\n- b\n", "a list"), ("just text\n", "a str")],
)
def test_the_top_level_must_be_a_mapping(pack, text, described):
    message = manifest_error(pack, text)

    assert "the top level must be a mapping" in message
    assert message.endswith(f"not {described}")


def test_name_and_version_are_required(pack):
    message = manifest_error(pack, "description: nothing else\n")

    assert message.splitlines()[1:] == ["  name: Field required", "  version: Field required"]


@pytest.mark.parametrize("name", ["Acme", "-acme", "ac me", "ac.me", "", "acmé"])
def test_a_pack_name_is_lowercase_letters_digits_underscore_and_hyphen(pack, name):
    message = manifest_error(pack, f'name: "{name}"\nversion: "1"\n')

    assert "  name: must start with a lowercase letter or digit" in message


@pytest.mark.parametrize("name", ["acme", "a", "claude-code", "acme_2", "9lives"])
def test_valid_pack_names(pack, name):
    (pack / "mcph-pack.yaml").write_text(f'name: {name}\nversion: "1"\n')

    assert load_manifest(pack).name == name


def test_the_version_must_be_a_string_so_two_point_ten_is_not_two_point_one(pack):
    message = manifest_error(pack, "name: acme\nversion: 2.10\n")  # a float for YAML

    assert "  version: Input should be a valid string" in message


def test_the_version_must_not_be_empty(pack):
    message = manifest_error(pack, 'name: acme\nversion: ""\n')

    assert "  version: String should have at least 1 character" in message


def test_what_this_mcph_does_not_support_yet_is_rejected(pack):
    message = manifest_error(pack, 'name: acme\nversion: "1"\nrunners:\n  - a.py\n')

    assert "  runners: Extra inputs are not permitted" in message


def test_rules_must_be_a_list(pack):
    message = manifest_error(pack, 'name: acme\nversion: "1"\nrules: rules/a.py\n')

    assert "  rules: Input should be a valid list" in message


@pytest.mark.parametrize(
    "file", ["/etc/rules.py", "../outside.py", "rules/../../outside.py", "rules/a.txt", "rules"]
)
def test_a_rule_file_must_be_a_python_file_inside_the_pack(pack, file):
    message = manifest_error(pack, f'name: acme\nversion: "1"\nrules:\n  - "{file}"\n')

    assert f"  rules: {file!r} must be a relative path to a .py file inside the pack" in message


def test_a_rule_file_must_not_be_listed_twice_even_spelled_differently(pack):
    message = manifest_error(
        pack, 'name: acme\nversion: "1"\nrules:\n  - rules/a.py\n  - ./rules/a.py\n'
    )

    assert "  rules: './rules/a.py' is listed twice" in message


def test_errors_inside_a_list_name_the_position(pack):
    message = manifest_error(pack, 'name: acme\nversion: "1"\nrules:\n  - 5\n')

    assert "  rules[0]: Input should be a valid string" in message
