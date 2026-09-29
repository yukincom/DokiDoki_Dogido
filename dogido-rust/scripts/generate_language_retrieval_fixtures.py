#!/usr/bin/env python3
"""正本DBの検索順・出典・部分成功を採取。外部通信・モデル・音声なし。"""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.language_dialogue.retrieval import LocalDialogueSearch
from dogido_server.reference_catalog import load_reference_index, search_references, get_reference
from dogido_server.knowledge_query import _ORGANIZATION_LABELS


def artifacts():
    cases = []
    def add(terms, facet="meaning", target=""):
        cases.append({"request": {"terms":terms,"facet":facet,"target":target},
                      "expected": asdict(LocalDialogueSearch().search(terms,facet=facet,target=target))})
    for term in ["枕詞","まくらことば","五 七 五","助詞","主語","仮名遣い","歴史的仮名遣い","変体仮名",
                 "川柳","俳句","現代詩","短歌","どぎど未収録","たくみ","ﾀｸﾐ","匠","は と が","こそ","～ば","〜ば"]:
        add([term], target=term)
    for target in ["三","3","３","二","匠","鬱","鿿","㍻"]:
        add([target],"grade",target)
    for facet in ["reading","spelling","comparison"]:
        add(["三"],facet,"三")
    for target in ["何年生で習う漢字", "森羅万象光", "三三", "3"]:
        add([],"grade",target)
    add(["  枕詞  ","枕詞","俳句","川柳","短歌","助詞","たくみ"])
    add(["たくみ 枕詞", "川柳 俳句"])
    add(["\x1c枕詞\x1f","マクラコトバ", "\U0001ccd6", "K"])
    add([])
    index = load_reference_index()
    catalog = []
    queries = ["", "学習指導要領 コード", "日本語 文型", "五 七 五", "  ＨＡＩＫＵ  ", "日本", "存在しない索引語", "\x1c枕詞\x1f"]
    # 索引にある全タイトルは、完全一致と安定IDによる同点順を比較する。
    queries += list(dict.fromkeys(e["title_ja"] for e in index["entries"].values()))
    for query in queries:
        catalog.append({"query":query,"filters":[],"limit":5,"expected":search_references(query,limit=5)})
    facets = {
        "dataset_ids":"by_dataset","kinds":"by_kind","tags":"by_tag","regions":"by_region",
        "languages":"by_language","source_ids":"by_source","entity_kinds":"by_entity_kind",
        "expression_modes":"by_expression_mode","formal_constraints":"by_formal_constraint",
        "prosodic_bases":"by_prosodic_basis","transmission_modes":"by_transmission_mode",
        "composition_modes":"by_composition_mode","education_stages":"by_education_stage",
        "subject_areas":"by_subject_area","acquisition_modes":"by_acquisition_mode",
        "machine_readable":"by_machine_readable","local_runtime_allowed":"by_local_runtime_allowed","providers":"by_provider",
    }
    for argument, facet in facets.items():
        for values in [[], list(index.get(facet,{}))[:2], [" 未収録 "]]:
            catalog.append({"query":"","filters":[[facet,values]],"limit":3,
                "expected":search_references("",limit=3,**{argument:values})})
    for limit in [0,1,2,20]:
        catalog.append({"query":"五 七 五","filters":[["by_dataset",[" JAPANESE_POETRY_FORMS ","world_poetry"]],["by_kind",["poetic_form"]]],
                        "limit":limit,"expected":search_references("五 七 五",dataset_ids=[" JAPANESE_POETRY_FORMS ","world_poetry"],kinds=["poetic_form"],limit=limit)})
    records = [{"id":id,"expected":get_reference(id)} for id in index["entries"]]
    records.append({"id":"absent:record", "expected":None})
    return {
        ROOT / "dogido-rust/fixtures/language-retrieval.json": {"source":"de57477 and unchanged canonical Python readers","lookups":cases,"catalog":catalog,"records":records},
        ROOT / "dogido-rust/src/knowledge/organization-labels.json": _ORGANIZATION_LABELS,
    }


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--check",action="store_true");args=parser.parse_args()
    for path,value in artifacts().items():
        rendered=json.dumps(value,ensure_ascii=False,indent=2)+"\n"
        if args.check:
            assert path.read_text()==rendered, f"stale {path.name}"
        else: path.write_text(rendered)
        if "lookups" in value: print(f"lookup {len(value['lookups'])}; catalog {len(value['catalog'])}; records {len(value['records'])}")


if __name__=="__main__": main()
