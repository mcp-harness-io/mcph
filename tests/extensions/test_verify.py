import pytest

from mcph.extensions import PackError, ResolvedPack, load_pack, verify_pack


def rule_source(*ids: str) -> str:
    parts = ["from mcph.extensions import doctor_rule\n"]
    for rule_id in ids:
        function = rule_id.replace(".", "_")
        parts.append(
            f'\n@doctor_rule(id="{rule_id}", description="Rule {rule_id}.")\n'
            f"def {function}(context):\n    return []\n"
        )
    return "".join(parts)


def test_a_good_pack_has_no_problems_and_comes_with_its_rules(write_pack):
    directory = write_pack(rules={"rules/a.py": rule_source("acme.a", "acme.b")})

    report = verify_pack(directory)

    assert report.problems == ()
    assert report.pack.manifest.name == "acme"
    assert report.pack.directory == directory
    assert [rule.id for rule in report.pack.rules] == ["acme.a", "acme.b"]


def test_a_pack_without_rule_files_is_valid(write_pack):
    report = verify_pack(write_pack())

    assert report.problems == () and report.pack.rules == ()


def test_a_directory_without_a_manifest_has_one_problem_and_no_pack(tmp_path):
    report = verify_pack(tmp_path)

    assert report.pack is None
    assert report.problems == (f"{tmp_path} is not a pack: it has no mcph-pack.yaml",)


def test_an_invalid_manifest_is_one_problem_that_lists_its_fields(write_pack):
    directory = write_pack(manifest='name: "Bad Name"\nrunners: []\n')

    report = verify_pack(directory)

    assert report.pack is None
    [problem] = report.problems
    assert problem.splitlines()[0] == f"invalid pack manifest {directory / 'mcph-pack.yaml'}:"
    assert "  name: must start with a lowercase letter or digit" in problem
    assert "  version: Field required" in problem
    assert "  runners: Extra inputs are not permitted" in problem


def test_every_problem_is_reported_in_the_order_of_the_manifest(write_pack):
    directory = write_pack(
        rules={
            "rules/missing.py": "",
            "rules/empty.py": "x = 1\n",
            "rules/broken.py": "def broken(:\n",
            "rules/good.py": rule_source("acme.good"),
            "rules/again.py": rule_source("acme.good"),
        }
    )
    (directory / "rules" / "missing.py").unlink()

    report = verify_pack(directory)

    assert len(report.problems) == 4
    assert "does not exist" in report.problems[0]
    assert "defines no rule" in report.problems[1]
    assert "cannot import broken.py: SyntaxError" in report.problems[2]
    assert report.problems[3] == (
        "pack 'acme': the rule id 'acme.good' is defined in rules/good.py and in rules/again.py"
    )
    assert [rule.id for rule in report.pack.rules] == ["acme.good"]  # what could be loaded


def test_loading_stops_at_the_first_problem_but_verifying_goes_on(write_pack, tmp_path):
    marker = tmp_path / "second-file-was-imported"
    second = f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n" + rule_source(
        "acme.second"
    )
    directory = write_pack(rules={"rules/first.py": "raise RuntimeError('boom')\n", "rules/second.py": second})

    with pytest.raises(PackError, match="cannot import first.py"):
        load_pack(ResolvedPack("./acme", directory))
    assert not marker.exists()  # the file after the problem was not imported

    report = verify_pack(directory)
    assert len(report.problems) == 1  # the second file is fine
    assert marker.exists()  # but verifying did run it


def test_the_bundled_core_pack_is_valid(core_pack):
    report = verify_pack(core_pack.directory)

    assert report.problems == ()
    assert [rule.id for rule in report.pack.rules] == [rule.id for rule in core_pack.rules]
