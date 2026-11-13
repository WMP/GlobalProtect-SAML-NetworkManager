"""
Tests for .github/scripts/release_notes.py, which puts the table of download
links at the top of a release's notes: a row per Ubuntu release, a column per
architecture, the packages of the pair in the cell. The table is built from
the assets of the release (`gh release view --json assets,body`) and merged
into its body between markers.

Every positive test has negative counterparts: bad input (repo, tag, asset
names and urls, markers, no packages) ends with "error: ..." on stderr, exit
status 1 and nothing on stdout, so the release never gets notes with a table
that silently lacks files or carries a link that is not a download link.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import json
import os
import re
import stat
import subprocess
import sys

import pytest
import workflow_steps

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, ".github", "scripts"))
import release_notes  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, ".github", "scripts", "release_notes.py")
REPO = "WMP/GlobalProtect-SAML-NetworkManager"
TAG = "v1.4.2"
VERSION = "1.4.2-1"
CODENAMES = {"jammy": "22.04", "noble": "24.04", "oracular": "24.10", "resolute": "26.04"}
PACKAGES = [
    "network-manager-gpclient",
    "network-manager-gpclient-gnome",
    "network-manager-gpclient-plasma",
]
PLASMA6 = "network-manager-gpclient-plasma-6"
BASE = "https://github.com/%s/releases/download/%s" % (REPO, TAG)
START, END = release_notes.START, release_notes.END
CS, CE = release_notes.CHANGES_START, release_notes.CHANGES_END
CHANGELOG = (
    "network-manager-gpclient (1.4.2) unstable; urgency=medium\n\n"
    "  * First change that runs over\n    two lines (#1)\n"
    "  * Second change (#2)\n\n"
    " -- Someone <a@example.org>  Fri, 13 Nov 2026 12:00:00 +0000\n\n"
    "network-manager-gpclient (1.4.1) unstable; urgency=medium\n\n"
    "  * An older change\n\n"
    " -- Someone <a@example.org>  Fri, 13 Nov 2025 12:00:00 +0000\n"
)
CHANGES = (CS + "\n## Changes in 1.4.2\n\n* First change that runs over two lines (#1)\n"
           "* Second change (#2)\n" + CE)


def asset_name(package, codename, arch, version=VERSION):
    """The name GitHub gives the asset: the "~" of the file name becomes "." """
    return "%s_%s.%s1_%s.deb" % (package, version, codename, arch)


def asset(name, url=None, state="uploaded"):
    return {"name": name, "url": url or "%s/%s" % (BASE, name), "size": 1, "state": state}


def full_matrix():
    return [asset(asset_name(p, c, a)) for c in CODENAMES for a in ("amd64", "arm64") for p in PACKAGES]


def debs(*specs, codename="noble", arch="amd64"):
    return [asset(asset_name(p, codename, arch)) for p in specs]


def link(package, codename, arch):
    short = "gpclient" if package == PACKAGES[0] else package[len(PACKAGES[0]) + 1:]
    return "[%s](%s/%s)" % (short, BASE, asset_name(package, codename, arch))


def write_json(tmp_path, assets, body="", raw=None):
    path = tmp_path / "release.json"
    path.write_text(raw if raw is not None else json.dumps({"assets": assets, "body": body}), encoding="utf-8")
    return str(path)


def write_changelog(tmp_path, text=CHANGELOG):
    path = tmp_path / "changelog"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run(tmp_path, assets, body="", repo=REPO, tag=TAG, raw=None, changelog=CHANGELOG):
    result = subprocess.run(
        [sys.executable, SCRIPT, "--repo", repo, "--tag", tag, "--release-json",
         write_json(tmp_path, assets, body, raw), "--changelog", write_changelog(tmp_path, changelog)],
        capture_output=True, check=False,
    )
    # decoded by hand: text=True would turn the "\r\n" of a body into "\n"
    result.stdout, result.stderr = result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
    return result


def refused(result, *words):
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    for word in words:
        assert word in result.stderr


def table_rows(output):
    return [line for line in output.splitlines() if line.startswith("|")]


def test_full_matrix_rows_columns_and_links(tmp_path):
    result = run(tmp_path, full_matrix())
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(CHANGES + "\n\n" + START + "\n## Downloads\n")
    assert result.stdout.endswith(END + "\n\n")
    rows = table_rows(result.stdout)
    assert rows[0] == "| Ubuntu | amd64 | arm64 |"
    assert rows[1] == "|---|---|---|"
    expected = []
    for codename, number in CODENAMES.items():  # known releases in version order
        cells = ["<br>".join(link(p, codename, a) for p in PACKAGES) for a in ("amd64", "arm64")]
        expected.append("| Ubuntu %s (%s) | %s | %s |" % (number, codename, cells[0], cells[1]))
    assert rows[2:] == expected


def test_links_are_the_urls_of_the_assets(tmp_path):
    name = asset_name(PACKAGES[0], "noble", "amd64")
    url = "https://github.com/%s/releases/download/untagged-abc123/%s" % (REPO, name)
    result = run(tmp_path, [asset(name, url)])
    assert result.returncode == 0, result.stderr
    assert "[gpclient](%s)" % url in result.stdout


@pytest.mark.parametrize("name", [
    "network-manager-gpclient-plasma_1.4.1-1.noble1_amd64.deb",
    "network-manager-gpclient_1.4.1-1~noble1_arm64.deb",
    "network-manager-gpclient-plasma_2.0.0.noble1_amd64.deb",
    "network-manager-gpclient_2.0.0~noble1_arm64.deb",
])
def test_both_spellings_of_the_asset_name_are_parsed(name):
    codename, arch, short = release_notes.parse(name)
    assert codename == "noble"
    assert arch in ("amd64", "arm64")
    assert short in ("plasma", "gpclient")


def test_text_of_the_notes_points_to_apt_and_the_install_command(tmp_path):
    out = run(tmp_path, full_matrix()).stdout
    assert "https://github.com/%s/blob/%s/docs/APT_REPO.md" % (REPO, TAG) in out
    assert "sudo apt install ./network-manager-gpclient_*.deb ./network-manager-gpclient-gnome_*.deb" in out


def test_text_of_the_notes_names_the_desktop_packages(tmp_path):
    out = run(tmp_path, full_matrix()).stdout
    assert "one desktop package (`-gnome`, `-plasma` or, on KDE neon, `-plasma-6`)" in out
    assert "-plasma-5" not in out


def test_text_of_the_notes_sends_ubuntu_22_04_to_the_readme_for_sdbus(tmp_path):
    out = run(tmp_path, full_matrix()).stdout
    readme = "https://github.com/%s/blob/%s/README.md" % (REPO, TAG)
    assert "Ubuntu 22.04 `python3-sdbus` is not in apt: see the [README](%s) first." % readme in out
    assert "pip3" not in out


def test_packages_in_a_cell_follow_the_fixed_order(tmp_path):
    names = ["zzz-extra", PACKAGES[2], PACKAGES[1], "aaa-extra", PACKAGES[0]]
    row = table_rows(run(tmp_path, debs(*names)).stdout)[2]
    assert re.findall(r"\[([^\]]+)\]", row) == ["gpclient", "gnome", "plasma", "aaa-extra", "zzz-extra"]


def test_plasma_6_for_kde_neon_follows_plasma_in_the_fixed_order(tmp_path):
    names = ["aaa-extra", PLASMA6, PACKAGES[2], PACKAGES[1], PACKAGES[0]]
    row = table_rows(run(tmp_path, debs(*names)).stdout)[2]
    assert re.findall(r"\[([^\]]+)\]", row) == ["gpclient", "gnome", "plasma", "plasma-6", "aaa-extra"]


def test_a_former_test_package_is_not_in_the_fixed_order(tmp_path):
    names = [PLASMA6, PACKAGES[2], "network-manager-gpclient-plasma-5", PACKAGES[0]]
    row = table_rows(run(tmp_path, debs(*names)).stdout)[2]
    # -plasma-5 is an unknown package: after the ones of the fixed order
    assert re.findall(r"\[([^\]]+)\]", row) == ["gpclient", "plasma", "plasma-6", "plasma-5"]


def test_the_order_has_plasma_6_after_plasma_and_nothing_of_the_former_split_before_it():
    assert release_notes.ORDER == ["gpclient", "gnome", "plasma", "plasma-6"]


def test_the_row_of_ubuntu_24_04_shows_both_plasma_packages_where_plasma_6_was_built(tmp_path):
    assets = full_matrix() + debs(PLASMA6)
    rows = table_rows(run(tmp_path, assets).stdout)
    noble = next(r for r in rows if "(noble)" in r)
    amd64, arm64 = noble.split("|")[2:4]
    assert [s for s in re.findall(r"\[([^\]]+)\]", amd64)] == ["gpclient", "gnome", "plasma", "plasma-6"]
    assert link(PLASMA6, "noble", "amd64") in amd64
    assert "plasma-6" not in arm64
    for row in rows:
        if row is not noble:
            assert "plasma-6" not in row


def test_without_plasma_6_no_row_shows_it(tmp_path):
    assert "plasma-6" not in " ".join(table_rows(run(tmp_path, full_matrix()).stdout))


def test_only_amd64_gives_a_single_column(tmp_path):
    rows = table_rows(run(tmp_path, debs(*PACKAGES)).stdout)
    assert rows[0] == "| Ubuntu | amd64 |"
    assert rows[1] == "|---|---|"
    assert "arm64" not in "\n".join(rows)


def test_missing_cell_is_a_dash_and_other_architectures_sort_after_amd64(tmp_path):
    assets = (debs(PACKAGES[0], arch="arm64") + debs(PACKAGES[0], arch="riscv64")
              + debs(PACKAGES[0], codename="jammy"))
    rows = table_rows(run(tmp_path, assets).stdout)
    assert rows[0] == "| Ubuntu | amd64 | arm64 | riscv64 |"
    assert rows[2].startswith("| Ubuntu 22.04 (jammy) | [gpclient]")
    assert rows[2].endswith("| — | — |")
    assert rows[3].startswith("| Ubuntu 24.04 (noble) | — | [gpclient]")


def test_unknown_codename_is_labelled_by_itself_and_sorted_last(tmp_path):
    assets = [a for c in ("zesty", "noble", "aardvark", "jammy") for a in debs(PACKAGES[0], codename=c)]
    labels = [row.split("|")[1].strip() for row in table_rows(run(tmp_path, assets).stdout)[2:]]
    assert labels == ["Ubuntu 22.04 (jammy)", "Ubuntu 24.04 (noble)", "aardvark", "zesty"]


# Assets that are not packages are ignored; a package that does not parse is an error

@pytest.mark.parametrize("name", [
    "README.md", "SHA256SUMS", "network-manager-gpclient_1.4.2-1.noble1_amd64.deb.sig",
    "source.tar.gz", "weird name;id.zip", "network-manager-gpclient_1.4.2-1.noble1_amd64.deb.asc",
])
def test_assets_that_are_not_debs_are_ignored(tmp_path, name):
    result = run(tmp_path, full_matrix() + [asset(name)])
    assert result.returncode == 0, result.stderr
    assert result.stdout == run(tmp_path, full_matrix()).stdout


@pytest.mark.parametrize("name", [
    "Network-Manager_1.4.2-1.noble1_amd64.deb",    # upper case
    "-gpclient_1.4.2-1.noble1_amd64.deb",
    "gpclient_1.4.2-1.noble1.deb",                 # no architecture
    "gpclient_1.4.2-1.noble1_amd64_x.deb",
    "gpclient_1.4.2-1.noble1_am d64.deb",
    "gpclient_1.4.2-1.noble1_amd64;id.deb",
    "gpclient_v1.4.2.noble1_amd64.deb",            # version does not start with a digit
    "gpclient_1.4.2-1_amd64.deb",                  # no codename suffix at all
    "gpclient_1.4.2-1.noble2_amd64.deb",           # not the release suffix
    "gpclient_1.4.2-1~Noble1_amd64.deb",
    "gpclient_1.4.2-1~noble_amd64.deb",
    "gpclient_1.4.2-1~noble1+pr24.57_amd64.deb",   # a pull request's build is not a release
    ".deb", "x.deb",
])
def test_deb_asset_that_does_not_match_is_an_error(tmp_path, name):
    refused(run(tmp_path, full_matrix() + [asset(name)]), ".deb")


@pytest.mark.parametrize("assets", [[], [asset("README.md"), asset("notes.txt")]])
def test_no_deb_assets_is_an_error(tmp_path, assets):
    refused(run(tmp_path, assets), "no .deb")


# Only an asset that has finished uploading has a link that works

@pytest.mark.parametrize("state", ["starter", "open", "", None, "Uploaded", "uploaded ", 1])
def test_asset_that_is_not_uploaded_is_not_linked(tmp_path, state):
    name = asset_name(PACKAGES[1], "noble", "amd64")
    result = run(tmp_path, [a for a in full_matrix() if a["name"] != name] + [asset(name, state=state)])
    expected = [a for a in full_matrix() if a["name"] != name]
    assert result.returncode == 0, result.stderr
    assert result.stdout == run(tmp_path, expected).stdout
    assert name not in result.stdout


@pytest.mark.parametrize("state", ["starter", "open", None])
def test_asset_without_an_upload_leaves_no_link_at_all(tmp_path, state):
    assets = debs(*PACKAGES)
    assets[1] = asset(asset_name(PACKAGES[1], "noble", "amd64"), state=state)
    out = run(tmp_path, assets).stdout
    assert "[gnome]" not in out and "[gpclient]" in out and "[plasma]" in out


def test_asset_with_a_missing_state_is_not_linked(tmp_path):
    assets = debs(*PACKAGES)
    del assets[1]["state"]
    out = run(tmp_path, assets).stdout
    assert "[gnome]" not in out and "[plasma]" in out


def test_uploaded_asset_is_linked(tmp_path):
    assert "[gnome](" in run(tmp_path, debs(*PACKAGES)).stdout


@pytest.mark.parametrize("state", ["starter", "open", None])
def test_only_assets_not_uploaded_is_the_no_deb_error(tmp_path, state):
    refused(run(tmp_path, [asset(asset_name(PACKAGES[0], "noble", "amd64"), state=state)]), "no .deb")


def test_a_url_of_an_asset_that_is_not_uploaded_is_not_checked(tmp_path):
    name = asset_name(PACKAGES[1], "noble", "amd64")
    result = run(tmp_path, full_matrix() + [asset(name, "http://evil", state="starter")])
    assert result.returncode == 0, result.stderr


# Two files for one package, release and architecture would make the table ambiguous

@pytest.mark.parametrize("first, second", [
    ("network-manager-gpclient_1.4.2-1.noble1_amd64.deb", "network-manager-gpclient_1.4.2-2.noble1_amd64.deb"),
    ("network-manager-gpclient_1.4.2-1.noble1_amd64.deb", "network-manager-gpclient_1.4.2-1~noble1_amd64.deb"),
    ("network-manager-gpclient_1.4.2-1.noble1_amd64.deb", "network-manager-gpclient_1.4.2-1.noble1_amd64.deb"),
])
def test_two_assets_for_the_same_package_release_and_arch_are_an_error(tmp_path, first, second):
    refused(run(tmp_path, [asset(first), asset(second)]), "same package", first, second)


@pytest.mark.parametrize("other", [
    "network-manager-gpclient_1.4.2-2.noble1_arm64.deb",             # other architecture
    "network-manager-gpclient_1.4.2-2.jammy1_amd64.deb",             # other release
    "network-manager-gpclient-gnome_1.4.2-2.noble1_amd64.deb",       # other package
])
def test_same_version_numbers_of_different_cells_are_fine(tmp_path, other):
    result = run(tmp_path, [asset("network-manager-gpclient_1.4.2-1.noble1_amd64.deb"), asset(other)])
    assert result.returncode == 0, result.stderr


def test_a_duplicate_that_is_not_uploaded_is_not_a_duplicate(tmp_path):
    old = asset("network-manager-gpclient_1.4.2-1.noble1_amd64.deb", state="starter")
    result = run(tmp_path, [old, asset("network-manager-gpclient_1.4.2-2.noble1_amd64.deb")])
    assert result.returncode == 0, result.stderr
    assert "1.4.2-2" in result.stdout and "1.4.2-1" not in result.stdout


# The url of an asset ends up in markdown: it must be its download link and nothing else

def bad_url_cases():
    name = asset_name(PACKAGES[0], "noble", "amd64")
    good = "https://github.com/%s/releases/download/%s/%s" % (REPO, TAG, name)
    return [
        good.replace(REPO, "evil/repo"),                       # other repo
        good.replace("https://github.com", "https://evil.example"),  # other host
        good.replace("https://", "http://"),
        good.replace(name, asset_name(PACKAGES[1], "noble", "amd64")),  # other file name
        good + ".sig",
        "https://github.com/%s/releases/download/%s" % (REPO, name),   # no tag folder
        "https://github.com/%s/releases/download//%s" % (REPO, name),
        "https://github.com/%s/releases/download/a/b/%s" % (REPO, name),
        good + ")[x](http://evil",
        good.replace("/" + name, ")/" + name),
        good.replace(TAG, "v1]"),
        good.replace(TAG, "v 1"),
        good.replace(TAG, "v1<b>"),
        good.replace(TAG, "v1\n"),
        good + "\n",
        good + "?x=1",
        good.replace(TAG, "v1\"x"),
        good.replace(TAG, "v1`id`"),
        "",
        None,
        42,
    ]


@pytest.mark.parametrize("url", bad_url_cases())
def test_bad_url_is_an_error(tmp_path, url):
    name = asset_name(PACKAGES[0], "noble", "amd64")
    refused(run(tmp_path, full_matrix()[1:] + [{"name": name, "url": url, "state": "uploaded"}]), "url")


def test_a_url_with_a_missing_key_is_an_error(tmp_path):
    refused(run(tmp_path, [{"name": asset_name(PACKAGES[0], "noble", "amd64"), "state": "uploaded"}]), "url")


def test_asset_without_a_name_is_an_error(tmp_path):
    refused(run(tmp_path, [{"url": BASE + "/x.deb"}]), "name")


def test_the_tag_folder_of_the_url_may_be_untagged_or_percent_encoded(tmp_path):
    name = asset_name(PACKAGES[0], "noble", "amd64")
    for folder in ("untagged-0123abcd", "v1.4.2%2Brc1"):
        url = "https://github.com/%s/releases/download/%s/%s" % (REPO, folder, name)
        result = run(tmp_path, [asset(name, url)])
        assert result.returncode == 0, result.stderr
        assert url in result.stdout


# repo and tag

@pytest.mark.parametrize("repo", ["", "owner", "owner/", "/repo", "a/b/c", "owner/re po", "o/r;rm", "o/r\n", "o/$(id)"])
def test_bad_repo_is_refused(tmp_path, repo):
    refused(run(tmp_path, full_matrix(), repo=repo), "repo")


@pytest.mark.parametrize("tag", ["v1.4.2", "v1.4.2+1", "v1.4.2-rc.1", "v1", "1.4.2", "main"])
def test_any_tag_is_accepted(tmp_path, tag):
    result = run(tmp_path, full_matrix(), tag=tag, changelog=CHANGELOG.replace("(1.4.2)", "(%s)" % tag.removeprefix("v")))
    assert result.returncode == 0, result.stderr
    assert "/blob/%s/docs/APT_REPO.md" % tag.replace("+", "%2B") in result.stdout
    assert "/blob/%s/README.md" % tag.replace("+", "%2B") in result.stdout


@pytest.mark.parametrize("tag, encoded", [
    ("v1\n# injected", "v1%0A%23%20injected"),
    ("v1)[x](http://evil", "v1%29%5Bx%5D%28http%3A//evil"),
    ("v1 <b>`id`", "v1%20%3Cb%3E%60id%60"),
    ("v1\r", "v1%0D"),
])
def test_tag_with_special_characters_is_percent_encoded_in_the_link(tag, encoded):
    out = release_notes.render(REPO, tag, full_matrix())  # a tag like this never reaches the changelog check
    assert "https://github.com/%s/blob/%s/docs/APT_REPO.md" % (REPO, encoded) in out
    assert "https://github.com/%s/blob/%s/README.md" % (REPO, encoded) in out
    assert tag not in out
    assert out.count(START) == 1 and out.count("\n## ") == 1


def test_a_slash_in_the_tag_stays_a_slash_in_the_links():
    out = release_notes.render(REPO, "v2.0/beta", full_matrix())
    assert "(https://github.com/%s/blob/v2.0/beta/docs/APT_REPO.md)" % REPO in out
    assert "(https://github.com/%s/blob/v2.0/beta/README.md)" % REPO in out
    assert "%2F" not in out


def test_empty_tag_is_an_error(tmp_path):
    refused(run(tmp_path, full_matrix(), tag=""), "tag")


def test_parse_gives_codename_arch_and_short_name():
    assert release_notes.parse("gpclient_1.4.2-1~noble1_amd64.deb") == ("noble", "amd64", "gpclient")
    assert release_notes.parse("network-manager-gpclient-gnome_1.4.2-1.noble1_arm64.deb") == ("noble", "arm64", "gnome")


@pytest.mark.parametrize("name", [
    "gpclient_1.4.2-1~noble2_amd64.deb", "gpclient_1.4.2-1_amd64.deb", "gpclient_1.4.2~1_amd64.deb",
    "gpclient_~noble1_amd64.deb", "x", "",
])
def test_parse_raises_notes_error_and_nothing_else(name):
    with pytest.raises(release_notes.NotesError):
        release_notes.parse(name)


# Merging the table into the body of the release

BLOCK = release_notes.render(REPO, TAG, full_matrix())
TOP = CHANGES + "\n\n" + BLOCK + "\n\n"
GENERATED = "## What's Changed\r\n* A fix by @someone in #1\n\n\n**Full Changelog**: https://x/compare/v1...v2  \n"


def body_of(tmp_path, old, assets=None):
    result = run(tmp_path, full_matrix() if assets is None else assets, body=old)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_output_is_the_block_on_top_of_a_body_without_one(tmp_path):
    assert body_of(tmp_path, GENERATED) == TOP + GENERATED  # the rest byte for byte


@pytest.mark.parametrize("raw", ['{"assets": %s}', '{"assets": %s, "body": null}', '{"assets": %s, "body": ""}'])
def test_missing_null_and_empty_body_are_the_same(tmp_path, raw):
    result = run(tmp_path, None, raw=raw % json.dumps(full_matrix()))
    assert result.returncode == 0, result.stderr
    assert result.stdout == TOP


def test_output_replaces_exactly_the_block_and_keeps_the_rest(tmp_path):
    old = "intro\r\n\n" + START + "\nold table\n" + END + "\n\n" + GENERATED
    out = body_of(tmp_path, old)
    assert out == CHANGES + "\n\nintro\r\n\n" + BLOCK + "\n\n" + GENERATED
    assert "old table" not in out


def test_running_twice_gives_the_same_body(tmp_path):
    once = body_of(tmp_path, GENERATED)
    twice = body_of(tmp_path, once)
    assert twice == once
    assert once.count(START) == 1


def test_a_table_of_an_earlier_run_is_replaced(tmp_path):
    first = body_of(tmp_path, GENERATED, assets=debs(PACKAGES[0]))
    assert body_of(tmp_path, first) == TOP + GENERATED


S, E = START, END


@pytest.mark.parametrize("old", [
    "text\n" + S + "\nno end\n",                  # start without end
    "text\n" + E + "\n",                          # end without start
    "text\n" + CS + "\nno end\n",                 # changes start without end
    CE + "\n",                                    # changes end without start
    CE + "\nx\n" + CS + "\n",                    # changes end before start
    CS + "\n" + CE + "\n" + CS + "\n" + CE,      # two changes blocks
    CS + "\n" + CS + "\n" + CE + "\n",           # two changes starts
    CS + "\n" + CE + "\n" + CE + "\n",           # two changes ends
    E + "\nx\n" + S + "\n",                      # end before start
    S + "\na\n" + E + "\n" + S + "\nb\n" + E,   # two blocks
    S + "\n" + S + "\n" + E + "\n",              # two starts
    S + "\n" + E + "\n" + E + "\n",              # two ends
])
def test_malformed_markers_in_the_body_are_an_error(tmp_path, old):
    refused(run(tmp_path, full_matrix(), body=old), "marker")


@pytest.mark.parametrize("raw", [
    "", "not json", "[]", "{}", '{"assets": null}', '{"assets": {}}', '{"assets": [], "body": 5}',
    '{"assets": ["x"]}', '{"assets": [1]}',
])
def test_bad_release_json_is_an_error(tmp_path, raw):
    refused(run(tmp_path, None, raw=raw))


def test_missing_json_file_is_an_error(tmp_path):
    result = subprocess.run([sys.executable, SCRIPT, "--repo", REPO, "--tag", TAG, "--release-json",
                             str(tmp_path / "nope"), "--changelog", write_changelog(tmp_path)],
                            capture_output=True, text=True, check=False)
    refused(result)


def test_missing_changelog_file_is_an_error_without_a_traceback(tmp_path):
    result = subprocess.run([sys.executable, SCRIPT, "--repo", REPO, "--tag", TAG, "--release-json",
                             write_json(tmp_path, full_matrix()), "--changelog", str(tmp_path / "nope")],
                            capture_output=True, text=True, check=False)
    refused(result)
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("args", [
    ["--repo", REPO, "--tag", TAG, "--release-json", "x"],         # no changelog
    ["--repo", REPO, "--tag", TAG, "--changelog", "x"],            # no JSON
    ["--repo", REPO, "--release-json", "x", "--changelog", "x"],   # no tag
    ["--tag", TAG, "--release-json", "x", "--changelog", "x"],     # no repo
    ["--merge", "x", "--table", "y"],                              # the old form
    [],
])
def test_wrong_combination_of_options_is_refused(args):
    result = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, check=False)
    assert result.returncode == 2
    assert result.stdout == b""


# --- The changes: the top entry of debian/changelog ----------------------------

def changelog_of(*bullet_lines, header="network-manager-gpclient (1.4.2) unstable; urgency=medium"):
    return header + "\n\n" + "\n".join(bullet_lines) + "\n\n -- X <x@example.org>  Fri, 13 Nov 2026 12:00:00 +0000\n"


def test_changes_block_joins_the_lines_of_a_bullet_and_stops_at_the_trailer():
    assert release_notes.changes_block(TAG, CHANGELOG) == CHANGES
    assert "older" not in CHANGES


def test_changes_of_the_real_changelog_match_its_top_version():
    text = release_notes.read_text(os.path.join(REPO_ROOT, "debian", "changelog"))
    version = re.match(r"\S+ \(([^)]+)\)", text).group(1)
    block = release_notes.changes_block("v" + version, text)
    assert block.startswith(CS + "\n## Changes in %s\n\n* " % version) and block.endswith(CE)
    assert all(line.startswith("* ") for line in block.splitlines()[3:-1])
    with pytest.raises(release_notes.NotesError):
        release_notes.changes_block("v" + version + ".1", text)


def test_the_order_is_changes_then_downloads_then_the_rest(tmp_path):
    out = body_of(tmp_path, GENERATED)
    assert out.index(CE) < out.index(START) < out.index(END) < out.index(GENERATED)
    assert out.startswith(CS)


def test_a_changes_block_is_replaced_in_place(tmp_path):
    old = "intro\n\n" + CS + "\nold changes\n" + CE + "\n\n" + BLOCK + "\n\n" + GENERATED
    out = body_of(tmp_path, old)
    assert out == "intro\n\n" + CHANGES + "\n\n" + BLOCK + "\n\n" + GENERATED
    assert "old changes" not in out


def test_downloads_are_added_right_after_an_existing_changes_block(tmp_path):
    out = body_of(tmp_path, "intro\n\n" + CS + "\nold\n" + CE + "\n\n" + GENERATED)
    assert out == "intro\n\n" + CHANGES + "\n\n" + BLOCK + "\n\n" + GENERATED


def test_changes_go_to_the_top_of_a_body_that_has_only_the_downloads(tmp_path):
    out = body_of(tmp_path, "intro\n" + START + "\nold\n" + END + "\nrest")
    assert out == CHANGES + "\n\n" + "intro\n" + BLOCK + "\nrest"


def test_a_rerun_on_its_own_output_changes_nothing(tmp_path):
    once = body_of(tmp_path, GENERATED)
    assert body_of(tmp_path, once) == once
    assert once.count(CS) == 1 and once.count(CE) == 1


@pytest.mark.parametrize("changelog, word", [
    (CHANGELOG.replace("(1.4.2)", "(1.4.3)"), "1.4.3"),                      # not the tag
    (CHANGELOG.replace("(1.4.2)", "(v1.4.2)"), "v1.4.2"),
    (changelog_of(), "no changes"),
    (changelog_of("", "  (no bullets)"), "no changes"),
    ("not a header\n\n  * x\n", "header"),
    ("", "header"),
    (changelog_of("  * x", header="network-manager-gpclient 1.4.2 unstable; urgency=medium"), "header"),
    (" -- X\n" + CHANGELOG, "header"),
    (changelog_of("  * a " + CS), "marker"),
    (changelog_of("  * a", "    continues " + CE.replace("changes", "changes")), "marker"),
    (changelog_of("  * a <!-- downloads:end -->"), "marker"),
    (changelog_of("  * a <!-- downloads:start -->"), "marker"),
])
def test_a_bad_changelog_is_an_error(tmp_path, changelog, word):
    refused(run(tmp_path, full_matrix(), changelog=changelog), word)


# The map of Ubuntu releases must follow the build matrix and the apt repository

def matrix_versions():
    with open(os.path.join(workflow_steps.WORKFLOWS, "build-release.yml"), encoding="utf-8") as handle:
        text = handle.read()
    # the line of the Ubuntu matrix: the RPM build has its own (tests/unit/test_build_rpm_workflow.py)
    ubuntu_lines = [line for line in text.splitlines() if line.startswith("        ubuntu: ")]
    assert len(ubuntu_lines) == 1, "expected one Ubuntu matrix line"
    found = {tuple(json.loads(m)) for m in re.findall(r"fromJSON\('(\[[^']*\])'\)", ubuntu_lines[0])}
    assert len(found) == 1, "the matrix is listed in more than one way: %s" % (found,)
    return set(found.pop())


def apt_suites():
    with open(os.path.join(REPO_ROOT, ".github", "scripts", "build-apt-repo.sh"), encoding="utf-8") as handle:
        match = re.search(r"^SUITES=\(([^)]*)\)", handle.read(), re.M)
    assert match, "build-apt-repo.sh does not list SUITES"
    return set(match.group(1).split())


# Releases that are not built any more: their packages stay in older GitHub releases and in the
# apt suites, so the notes of those releases still need the name
RETIRED = {"24.10"}


def map_problems(ubuntu, versions, suites):
    problems = ["no codename for Ubuntu %s" % v for v in sorted(versions - set(ubuntu.values()))]
    problems += ["no entry for the suite %s" % c for c in sorted(suites - set(ubuntu))]
    problems += ["%s is in neither the matrix nor the suites" % c for c in sorted(set(ubuntu) - suites)]
    problems += ["%s (%s) is not in the build matrix" % (c, v) for c, v in sorted(ubuntu.items()) if v not in versions | RETIRED]
    return problems


def test_ubuntu_map_matches_the_build_matrix_and_the_apt_suites():
    assert map_problems(release_notes.UBUNTU, matrix_versions(), apt_suites()) == []


def test_a_retired_release_is_in_the_map_but_not_in_the_matrix():
    assert "24.10" in release_notes.UBUNTU.values()
    assert not RETIRED & matrix_versions()


def test_map_check_reports_a_missing_entry():
    ubuntu = dict(release_notes.UBUNTU)
    del ubuntu["noble"]
    problems = map_problems(ubuntu, matrix_versions(), apt_suites())
    assert any("24.04" in p for p in problems) and any("noble" in p for p in problems)


def test_map_check_reports_an_entry_without_a_build():
    ubuntu = dict(release_notes.UBUNTU, zesty="99.04")
    assert map_problems(ubuntu, matrix_versions(), apt_suites())


# The workflow: the job "release-notes" runs this script on the published release.

def job_text(name):
    with open(os.path.join(workflow_steps.WORKFLOWS, "build-release.yml"), encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("\n  %s:\n" % name) + 1
    match = re.search(r"^  (#|[a-z][a-z-]*:\n)", text[start + 1:], re.M)
    return text[start:start + 1 + match.start()] if match else text[start:]


NOTES_STEP = "Add downloads table to the release notes"


def test_release_job_does_not_know_about_the_notes():
    job = job_text("release")
    for word in ("checkout", "body_path", "release_notes.py", "release-notes", "downloads"):
        assert word not in job.lower()


def test_release_notes_job_runs_after_release_for_tags_only_with_write_access():
    job = job_text("release-notes")
    assert "\n    needs: release\n" in job
    assert "\n    if: startsWith(github.ref, 'refs/tags/') && github.event_name == 'push'\n" in job
    assert "\n    runs-on: ubuntu-latest\n" in job
    assert "\n    permissions:\n      contents: write\n" in job


def test_release_notes_job_is_between_release_and_publish_apt():
    with open(os.path.join(workflow_steps.WORKFLOWS, "build-release.yml"), encoding="utf-8") as handle:
        text = handle.read()
    assert text.index("\n  release:\n") < text.index("\n  release-notes:\n") < text.index("\n  publish-apt:\n")


def test_publish_apt_needs_only_release_so_the_notes_never_block_it():
    job = job_text("publish-apt")
    assert "\n    needs: release\n" in job
    assert "release-notes" not in job


def test_release_notes_job_checks_out_only_the_scripts_without_credentials():
    job = job_text("release-notes")
    start = job.index("- name: Checkout code")
    step = job[start:job.index("\n      - name:", start + 1)]
    assert "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in step
    assert "persist-credentials: false" in step
    assert "sparse-checkout: |\n            .github/scripts\n            debian\n" in step
    assert "sparse-checkout-cone-mode" not in step.replace("sparse-checkout-cone-mode: true", "")


def test_notes_step_passes_token_repo_and_tag_by_env_and_no_expression_in_the_script():
    step = "\n".join(workflow_steps.step_lines("build-release.yml", NOTES_STEP))
    assert "GH_TOKEN: ${{ github.token }}" in step
    assert "REPO: ${{ github.repository }}" in step
    assert "TAG: ${{ github.ref_name }}" in step
    assert "${{" not in step[step.index("run: |"):]
    assert "--changelog debian/changelog" in step


FAKE_GH = """#!/bin/bash
echo "$@" >> "$GH_LOG"
if [ "$1 $2" = "release view" ]; then
    [ -n "$GH_VIEW_FAILS" ] && exit 1
    printf '%s' "$GH_JSON"
elif [ "$1 $2" = "release edit" ]; then
    while [ $# -gt 0 ]; do
        [ "$1" = "--notes-file" ] && cp "$2" "$GH_EDITED"
        shift
    done
fi
"""


def run_notes_step(tmp_path, body, assets=None, view_fails=False):
    """The step's script under `bash -e` (as Actions runs it, without pipefail) with a fake gh"""
    script = workflow_steps.step_script("build-release.yml", NOTES_STEP)
    work = tmp_path / "work"
    (work / ".github").mkdir(parents=True)
    os.symlink(os.path.join(REPO_ROOT, ".github", "scripts"), work / ".github" / "scripts")
    (work / "debian").mkdir()
    (work / "debian" / "changelog").write_text(CHANGELOG, encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    release = json.dumps({"assets": full_matrix() if assets is None else assets, "body": body})
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), REPO=REPO, TAG=TAG, GH_TOKEN="t",
               GH_LOG=str(tmp_path / "gh.log"), GH_EDITED=str(tmp_path / "edited.md"), GH_JSON=release)
    if view_fails:
        env["GH_VIEW_FAILS"] = "1"
    result = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env,
                            capture_output=True, text=True, check=False)
    log = (tmp_path / "gh.log").read_text() if (tmp_path / "gh.log").exists() else ""
    return result, log, tmp_path / "edited.md"


def test_notes_step_edits_the_release_with_the_merged_body(tmp_path):
    result, log, edited = run_notes_step(tmp_path, GENERATED)
    assert result.returncode == 0, result.stderr
    assert "release view %s --repo %s --json assets,body" % (TAG, REPO) in log
    assert "release edit %s --repo %s --notes-file" % (TAG, REPO) in log
    assert edited.read_bytes().decode() == TOP + GENERATED


def test_notes_step_on_a_rerun_gives_the_same_body(tmp_path):
    first = TOP + GENERATED
    result, _log, edited = run_notes_step(tmp_path, first)
    assert result.returncode == 0, result.stderr
    assert edited.read_bytes().decode() == first


def test_notes_step_fails_and_does_not_edit_when_the_release_cannot_be_read(tmp_path):
    result, log, edited = run_notes_step(tmp_path, GENERATED, view_fails=True)
    assert result.returncode != 0
    assert "release edit" not in log
    assert not edited.exists()


@pytest.mark.parametrize("assets, body", [
    ([asset("network-manager-gpclient_1.4.2.deb")], GENERATED),                        # a bad asset
    ([asset(asset_name(PACKAGES[0], "noble", "amd64"), BASE + "/other.deb")], GENERATED),  # a bad url
    ([asset("README.md")], GENERATED),                                                 # no packages
    (None, GENERATED + START + "\n"),                                                  # bad markers
    (None, GENERATED + CS + "\n"),                                                     # bad changes markers
])
def test_notes_step_does_not_edit_when_the_script_fails(tmp_path, assets, body):
    result, log, edited = run_notes_step(tmp_path, body, assets=assets)
    assert result.returncode != 0
    assert "release edit" not in log
    assert not edited.exists()


# --- RPM packages: a second table, Ubuntu output unchanged ---------------------

RPM_TARGETS = {"fc43": "Fedora 43", "fc44": "Fedora 44", "fc45": "Fedora 45", "el10": "EL 10",
               "lp160": "openSUSE Leap 16.0", "lp161": "openSUSE Leap 16.1", "tw": "openSUSE Tumbleweed"}


def rpm_asset_name(package, dist, arch, version="1.4.2"):
    return "%s-%s-1.%s.%s.rpm" % (package, version, dist, arch)


def rpm_matrix(dists=tuple(RPM_TARGETS)):
    return [asset(rpm_asset_name(p, d, a)) for d in dists for a in ("x86_64", "aarch64") for p in PACKAGES]


def test_rpm_asset_names_are_parsed():
    assert release_notes.parse_rpm(rpm_asset_name(PACKAGES[1], "lp160", "aarch64")) == ("lp160", "aarch64", "gnome")
    assert release_notes.parse_rpm(rpm_asset_name(PACKAGES[0], "fc44", "x86_64")) == ("fc44", "x86_64", "gpclient")


@pytest.mark.parametrize(
    "name",
    [
        "network-manager-gpclient-1.4.2-1.fc44.src.rpm",
        "network-manager-gpclient-1.4.2-1.fc44.i686.rpm",
        "network-manager-gpclient-1.4.2-1.fc44.noarch.rpm",
        "network-manager-gpclient-debuginfo-1.4.2-1.fc44.x86_64.rpm",
        "network-manager-gpclient-debugsource-1.4.2-1.fc44.x86_64.rpm",
        "network-manager-gpclient-plasma-6-1.4.2-1.fc44.x86_64.rpm",
        "network-manager-gpclient-1.4.2-1.xx44.x86_64.rpm",
        "network-manager-gpclient-1.4.2-1.fc44.pr24.57.x86_64.rpm",
        "other-package-1.4.2-1.fc44.x86_64.rpm",
        "network-manager-gpclient-1.4.2-1.x86_64.rpm",
    ],
)
def test_an_rpm_name_that_does_not_match_is_not_parsed(name):
    with pytest.raises(release_notes.NotesError):
        release_notes.parse_rpm(name)


def test_rpm_downloads_have_their_own_table_with_a_row_per_target(tmp_path):
    out = run(tmp_path, full_matrix() + rpm_matrix()).stdout
    rows = table_rows(out)
    assert "| RPM target | x86_64 | aarch64 |" in rows
    labels = [row.split(" | ")[0].lstrip("| ") for row in rows if row.startswith("| ") and "RPM target" not in row]
    rpm_labels = [label for label in labels if not label.startswith("Ubuntu")]
    assert rpm_labels == ["Fedora 43", "Fedora 44", "Fedora 45", "EL 10", "openSUSE Leap 16.0", "openSUSE Leap 16.1",
                          "openSUSE Tumbleweed"]
    row = next(r for r in rows if r.startswith("| Fedora 44 |"))
    assert row.count("[gpclient](") == 2 and row.count("[gnome](") == 2 and row.count("[plasma](") == 2
    assert "docs/RPM_REPO.md" in out


def test_the_ubuntu_table_is_the_same_with_and_without_rpms(tmp_path):
    only_debs = run(tmp_path, full_matrix()).stdout
    with_rpms = run(tmp_path, full_matrix() + rpm_matrix()).stdout
    deb_part = only_debs[len(START):only_debs.index(END)]
    assert deb_part in with_rpms
    assert "RPM target" not in only_debs
    assert "docs/RPM_REPO.md" not in only_debs


def test_rpms_only_have_no_apt_table(tmp_path):
    out = run(tmp_path, rpm_matrix()).stdout
    assert "| RPM target |" in out
    assert "| Ubuntu |" not in out and "APT_REPO" not in out


def test_source_and_unfinished_rpms_are_not_listed(tmp_path):
    source = asset("network-manager-gpclient-1.4.2-1.fc44.src.rpm")
    unfinished = asset(rpm_asset_name(PACKAGES[0], "fc99", "x86_64"), state="starter")
    out = run(tmp_path, full_matrix() + rpm_matrix(["fc44"]) + [source, unfinished]).stdout
    assert source["name"] not in out and unfinished["name"] not in out
    assert "Fedora 99" not in out


@pytest.mark.parametrize(
    "name",
    [
        "network-manager-gpclient-debuginfo-1.4.2-1.fc44.x86_64.rpm",
        "network-manager-gpclient-1.4.2-1.fc44.i686.rpm",
        "network-manager-gpclient-1.4.2-1.xx44.x86_64.rpm",
    ],
)
def test_a_binary_rpm_with_an_unexpected_name_is_an_error(tmp_path, name):
    refused(run(tmp_path, full_matrix() + rpm_matrix(["fc44"]) + [asset(name)]), ".rpm")


def test_two_rpms_for_the_same_package_target_and_architecture_are_an_error(tmp_path):
    again = asset(rpm_asset_name(PACKAGES[0], "fc44", "x86_64", version="1.4.3"))
    refused(run(tmp_path, rpm_matrix(["fc44"]) + [again]), "two assets")


def test_the_url_of_an_rpm_must_be_its_download_link(tmp_path):
    bad = asset(rpm_asset_name(PACKAGES[0], "fc44", "x86_64"), url="https://evil.example/x.rpm")
    refused(run(tmp_path, full_matrix() + [bad]), "url")


def test_no_deb_and_no_rpm_is_an_error(tmp_path):
    refused(run(tmp_path, [asset("network-manager-gpclient-1.4.2-1.fc44.src.rpm")]), "no .deb, binary .rpm or .pkg.tar.zst")


def test_the_rpm_install_hint_picks_one_target_and_the_core_package_only(tmp_path):
    out = run(tmp_path, rpm_matrix()).stdout
    assert "./network-manager-gpclient-[0-9]*.<dist>.<arch>.rpm" in out
    assert "./network-manager-gpclient-gnome-[0-9]*.<dist>.<arch>.rpm" in out
    assert "zypper install --allow-unsigned-rpm" in out
    assert "not signed" in out


def test_the_rpm_install_hint_glob_matches_only_the_core_package_of_a_target():
    import fnmatch
    names = [rpm_asset_name(p, d, a) for p in PACKAGES for d in ("fc44", "el10") for a in ("x86_64", "aarch64")]
    core = fnmatch.filter(names, "network-manager-gpclient-[0-9]*.fc44.x86_64.rpm")
    assert core == [rpm_asset_name(PACKAGES[0], "fc44", "x86_64")]
    gnome = fnmatch.filter(names, "network-manager-gpclient-gnome-[0-9]*.fc44.x86_64.rpm")
    assert gnome == [rpm_asset_name(PACKAGES[1], "fc44", "x86_64")]
    # the old pattern took the desktop packages and other targets with it
    assert len(fnmatch.filter(names, "network-manager-gpclient-*.rpm")) == len(names)


def test_the_deb_only_notes_have_no_rpm_hint(tmp_path):
    out = run(tmp_path, full_matrix()).stdout
    assert "zypper" not in out and "<dist>" not in out


# --- Arch Linux packages: a third table with one row ---------------------------


def arch_asset_name(package, version="1.4.2", revision="1"):
    return "%s-%s-%s-x86_64.pkg.tar.zst" % (package, version, revision)


def arch_matrix():
    return [asset(arch_asset_name(p)) for p in PACKAGES]


def test_arch_asset_names_are_parsed():
    assert release_notes.parse_arch(arch_asset_name(PACKAGES[0])) == "gpclient"
    assert release_notes.parse_arch(arch_asset_name(PACKAGES[2])) == "plasma"


@pytest.mark.parametrize(
    "name",
    [
        "network-manager-gpclient-1.4.2-1-aarch64.pkg.tar.zst",
        "network-manager-gpclient-1.4.2-1-any.pkg.tar.zst",
        "network-manager-gpclient-debug-1.4.2-1-x86_64.pkg.tar.zst",
        "network-manager-gpclient-1.4.2-1-x86_64.pkg.tar.xz",
        "network-manager-gpclient-1.4.2-1-x86_64.pkg.tar.zst.sig",
        "network-manager-gpclient-1.4.2-x86_64.pkg.tar.zst",
        "other-package-1.4.2-1-x86_64.pkg.tar.zst",
    ],
)
def test_an_arch_name_that_does_not_match_is_not_parsed(name):
    with pytest.raises(release_notes.NotesError):
        release_notes.parse_arch(name)


def test_arch_downloads_have_their_own_table_with_a_row(tmp_path):
    out = run(tmp_path, full_matrix() + rpm_matrix() + arch_matrix()).stdout
    rows = table_rows(out)
    assert "| Arch Linux | x86_64 |" in rows
    row = [r for r in rows if r.startswith("| Arch Linux | [")]
    assert len(row) == 1
    for short, package in zip(("gpclient", "gnome", "plasma"), PACKAGES, strict=True):
        assert "[%s](%s/%s)" % (short, BASE, arch_asset_name(package)) in row[0]
    assert "docs/ARCH_REPO.md" in out and "pacman -U" in out


def test_the_other_tables_are_the_same_with_and_without_arch(tmp_path):
    without = run(tmp_path, full_matrix() + rpm_matrix()).stdout
    with_arch = run(tmp_path, full_matrix() + rpm_matrix() + arch_matrix()).stdout
    assert without[:without.index(END)] in with_arch
    assert "Arch Linux" not in without and "ARCH_REPO" not in without


def test_arch_only_has_no_apt_and_no_rpm_table(tmp_path):
    out = run(tmp_path, arch_matrix()).stdout
    assert "| Arch Linux |" in out
    assert "| Ubuntu |" not in out and "| RPM target |" not in out


def test_unfinished_arch_packages_are_not_listed(tmp_path):
    unfinished = asset(arch_asset_name(PACKAGES[0], version="9.9.9"), state="starter")
    out = run(tmp_path, full_matrix() + arch_matrix() + [unfinished]).stdout
    assert unfinished["name"] not in out


@pytest.mark.parametrize("name", ["network-manager-gpclient-debug-1.4.2-1-x86_64.pkg.tar.zst",
                                  "network-manager-gpclient-1.4.2-1-aarch64.pkg.tar.zst"])
def test_an_arch_package_with_an_unexpected_name_is_an_error(tmp_path, name):
    refused(run(tmp_path, full_matrix() + arch_matrix() + [asset(name)]), ".pkg.tar.zst")


def test_two_arch_packages_with_the_same_name_are_an_error(tmp_path):
    again = asset(arch_asset_name(PACKAGES[0], version="1.4.3"))
    refused(run(tmp_path, arch_matrix() + [again]), "two assets")


def test_the_url_of_an_arch_package_must_be_its_download_link(tmp_path):
    bad = asset(arch_asset_name(PACKAGES[0]), url="https://evil.example/x.pkg.tar.zst")
    refused(run(tmp_path, full_matrix() + [bad]), "url")
