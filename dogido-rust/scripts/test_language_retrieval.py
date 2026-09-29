"""実DBの欠損・混在を用い、Rust検索と正本Pythonの部分成功を比較する。"""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.language_dialogue.retrieval import LocalDialogueSearch
from dogido_server.knowledge_query import _ORGANIZATION_LABELS

REFERENCE = ROOT / "reference/language_education_and_poetry"
CARDS = ROOT / "dogido_server/language_dialogue/source_cards.json"
BINARY = ROOT / "dogido-rust/target/debug/dogido-rust"
REQUEST = {"terms":["たくみ", "川柳"], "facet":"meaning", "target":"川柳"}


@pytest.fixture
def reference(tmp_path):
    base = tmp_path / "reference"
    base.mkdir()
    index = json.loads((REFERENCE/"index.json").read_text())
    shutil.copy2(REFERENCE/"index.json", base/"index.json")
    for row in index["datasets"]:
        shutil.copy2(REFERENCE/row["path"],base/row["path"])
    return base


def native(tmp_path, base, cards=CARDS, request=REQUEST):
    path=tmp_path/"requests.json"
    path.write_text(json.dumps([request],ensure_ascii=False))
    result=subprocess.run([str(BINARY),"lookup-language",str(path),"--reference-dir",str(base),"--cards-path",str(cards)],
        check=True,capture_output=True,text=True,timeout=20)
    return json.loads(result.stdout)[0]


def compare(tmp_path, reference, cards=CARDS, request=REQUEST):
    expected=asdict(LocalDialogueSearch(reference_dir=reference,cards_path=cards).search(
        request["terms"],facet=request["facet"],target=request["target"]))
    actual=native(tmp_path,reference,cards,request)
    assert actual==expected
    return actual


def write_index(base,index):
    (base/"index.json").write_text(json.dumps(index,ensure_ascii=False))


@pytest.mark.parametrize("facet",["by_term","by_term_ngram","by_dataset"])
def test_missing_index_table_keeps_cards_but_is_unavailable(tmp_path,reference,facet):
    index=json.loads((reference/"index.json").read_text());index.pop(facet)
    write_index(reference,index)
    request={**REQUEST,"terms":["たくみ","未収録語彙"]}
    result=compare(tmp_path,reference,request=request)
    assert result["status"]=="unavailable" and result["error"]=="KeyError"
    assert result["facts"][0]["id"]=="language.usage.takumi"


@pytest.mark.parametrize("facet,key",[("by_term","川柳"),("by_term_ngram","たくみ"),("by_dataset","japanese_poetry_forms")])
def test_null_index_values_are_corruption_instead_of_no_match(tmp_path,reference,facet,key):
    index=json.loads((reference/"index.json").read_text());index[facet][key]=None
    write_index(reference,index)
    result=compare(tmp_path,reference)
    assert result["status"]=="unavailable" and result["error"]=="TypeError"
    assert result["facts"][0]["id"]=="language.usage.takumi"


def test_partial_cards_survive_digest_failure(tmp_path,reference):
    index=json.loads((reference/"index.json").read_text())
    file=reference/index["datasets"][0]["path"]
    stat=file.stat(); original=file.read_bytes()
    new=file.with_suffix(".next")
    new.write_bytes(original.replace(b'"',b' ',1))
    import os
    os.utime(new,ns=(stat.st_atime_ns,stat.st_mtime_ns));new.replace(file)
    result=compare(tmp_path,reference)
    assert result["status"]=="unavailable" and result["error"]=="ValueError"
    assert result["facts"][0]["id"]=="language.usage.takumi"


@pytest.mark.parametrize("target",["index","dataset"])
def test_symlinked_artifact_is_unavailable(tmp_path,reference,target):
    index=json.loads((reference/"index.json").read_text())
    file=reference/("index.json" if target=="index" else index["datasets"][0]["path"])
    external=tmp_path/"external.json";file.rename(external);file.symlink_to(external)
    assert compare(tmp_path,reference)["status"]=="unavailable"


def test_curated_search_does_not_require_bulk_tables(tmp_path,reference):
    assert not (reference/"data").exists()
    result=compare(tmp_path,reference)
    assert result["status"]=="searched" and result["facts"]


@pytest.mark.parametrize("kind",["missing","broken"])
def test_cards_failure_does_not_invent_facts(tmp_path,reference,kind):
    cards=tmp_path/"cards.json"
    if kind=="broken": cards.write_text('{"records":')
    result=compare(tmp_path,reference,cards)
    assert result["status"]=="unavailable" and not result["facts"]


def test_missing_bulk_keeps_cards_and_is_not_no_match(tmp_path,reference):
    request={"terms":["たくみ","三"],"facet":"grade","target":"三"}
    result=compare(tmp_path,reference,request=request)
    assert result["status"]=="unavailable" and result["facts"][0]["id"]=="language.usage.takumi"


def test_source_organization_labels_match_canonical_reader():
    labels=json.loads((ROOT/"dogido-rust/src/knowledge/organization-labels.json").read_text())
    assert labels==_ORGANIZATION_LABELS
