import gzip
import io
import json
import os
import re
import sys
from pathlib import Path

import requests
from google.protobuf.json_format import MessageToDict
from ord_schema.proto import dataset_pb2
from ord_schema.message_helpers import fetch_dataset


def list_dataset_files(limit=None, token=None):
    url = "https://api.github.com/repos/open-reaction-database/ord-data/contents/data"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    files = [f for f in r.json() if f.get("name", "").endswith(".pb.gz")]
    files.sort(key=lambda x: x.get("name", ""))
    if limit is not None:
        files = files[:limit]
    return files


def load_dataset_from_bytes(pb_gz_bytes):
    with gzip.GzipFile(fileobj=io.BytesIO(pb_gz_bytes)) as gz:
        data = gz.read()
    ds = dataset_pb2.Dataset()
    ds.ParseFromString(data)
    return ds


def best_identifier(comp_dict):
    ids = comp_dict.get("identifiers", [])
    smiles = None
    for ident in ids:
        if ident.get("type") == "SMILES":
            smiles = ident.get("value")
            break
    if smiles is not None:
        return {"type": "SMILES", "value": smiles}
    if ids:
        ident = ids[0]
        return {"type": ident.get("type"), "value": ident.get("value")}
    return {"type": None, "value": None}


def classify(input_name):
    n = input_name.strip().lower().replace(" ", "_")
    if "base" in n:
        return "base"
    if "solvent" in n:
        return "solvent"
    if "amine" in n:
        return "amine"
    if "aryl" in n and "halide" in n:
        return "aryl_halide"
    if "carboxylic" in n and "acid" in n:
        return "carboxylic_acid"
    if "activation" in n and "agent" in n:
        return "activation_agent"
    if "additive" in n:
        return "additive"
    if "metal" in n and "ligand" in n:
        return "metal_and_ligand"
    if "metal" in n:
        return "metal"
    if "ligand" in n:
        return "ligand"
    if re.fullmatch(r"m[1-7](?:_m[1-7])*", n):
        return n
    return None


def extract_reaction(reaction):
    rdict = MessageToDict(reaction, preserving_proto_field_name=True, use_integers_for_enums=False)
    rid = rdict.get("reaction_id")
    out = {"reaction_id": rid, "base": [], "solvent": [], "amine": [], "aryl_halide": [], "carboxylic_acid": [], "additive": [], "activation_agent": [], "metal": [], "ligand": [], "metal_and_ligand": []}
    inputs = rdict.get("inputs", {})
    for in_name, in_msg in inputs.items():
        comps = in_msg.get("components", [])
        cat = classify(in_name)
        for comp in comps:
            ident = best_identifier(comp)
            role_field = comp.get("reaction_role")
            role = role_field.get("type") if isinstance(role_field, dict) else role_field
            item = {
                "category": cat,
                "value": ident["value"],
                "reaction_role": role,
            }
            if cat:
                if cat not in out:
                    out[cat] = []
                out[cat].append(item)
    out = {k: v for k, v in out.items() if not isinstance(v, list) or (isinstance(v, list) and len(v) > 0)}
    return out


def parse_dataset_id(arg):
    m = re.search(r"ord_dataset-[0-9a-f]{32}", arg)
    return m.group(0) if m else None


def download_dataset_by_id(dataset_id):
    raw_url = f"https://raw.githubusercontent.com/open-reaction-database/ord-data/main/data/{dataset_id}.pb.gz"
    r = requests.get(raw_url, timeout=60)
    if r.status_code == 200:
        return r.content
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api_url = "https://api.github.com/repos/open-reaction-database/ord-data/contents/data"
    resp = requests.get(api_url, headers=headers, timeout=30)
    resp.raise_for_status()
    for f in resp.json():
        name = f.get("name", "")
        if name.startswith(dataset_id) and name.endswith(".pb.gz"):
            dl = f.get("download_url")
            rb = requests.get(dl, timeout=60)
            rb.raise_for_status()
            return rb.content
    raise FileNotFoundError(f"Dataset {dataset_id} not found in ord-data repository")


def extract_one_dataset(dataset_id):
    try:
        ds = fetch_dataset(dataset_id)
    except Exception:
        pb = download_dataset_by_id(dataset_id)
        ds = load_dataset_from_bytes(pb)
    results = []
    dataset_id = ds.dataset_id if hasattr(ds, "dataset_id") else dataset_id
    for rxn in ds.reactions:
        rec = extract_reaction(rxn)
        rec["dataset_id"] = dataset_id
        results.append(rec)
    return results


def extract(limit_datasets=None, token=None):
    files = list_dataset_files(limit=limit_datasets, token=token)
    results = []
    for f in files:
        b = requests.get(f["download_url"], timeout=60).content
        ds = load_dataset_from_bytes(b)
        dataset_id = ds.dataset_id if hasattr(ds, "dataset_id") else None
        for rxn in ds.reactions:
            rec = extract_reaction(rxn)
            rec["dataset_id"] = dataset_id
            results.append(rec)
    return results


def main():
    out = Path(os.getenv("OUTPUT", "ord_raw_components.json"))
    args = sys.argv[1:]
    if args:
        dsid = parse_dataset_id(args[0])
        if not dsid:
            return
        data = extract_one_dataset(dsid)
    else:
        limit = os.getenv("LIMIT_DATASETS")
        limit_datasets = int(limit) if limit else None
        token = os.getenv("GITHUB_TOKEN")
        data = extract(limit_datasets=limit_datasets, token=token)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

