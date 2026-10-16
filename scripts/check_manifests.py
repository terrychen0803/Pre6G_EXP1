"""Offline YAML, bash syntax and optional cached Kubernetes OpenAPI validation."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import json
import jsonschema
import yaml


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def check(path: Path, schema_dir: Path | None = None) -> int:
    count = 0
    for document in yaml.load_all(path.read_text(), Loader=UniqueLoader):
        if not document:
            continue
        if document.get("kind") not in {"Job", "Pod", "PersistentVolumeClaim"}:
            raise ValueError(f"{path}: expected Job, Pod or PersistentVolumeClaim")
        if schema_dir:
            schema = json.loads((schema_dir / ({"Job": "job", "Pod": "pod", "PersistentVolumeClaim": "pvc"}[document["kind"]] + ".json")).read_text())
            jsonschema.validate(document, schema)
        if document["kind"] == "PersistentVolumeClaim":
            count += 1
            continue
        spec = document["spec"]["template"]["spec"] if document["kind"] == "Job" else document["spec"]
        for container in [*spec.get("initContainers", []), *spec["containers"]]:
            command = container.get("command", [])
            if command[:2] == ["/bin/bash", "-lc"]:
                subprocess.run(["bash", "-n"], input=command[2], text=True, check=True, capture_output=True)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--kubernetes-schema-dir", type=Path,
                        help="Offline directory containing standalone strict job.json and pod.json")
    args = parser.parse_args()
    count = sum(check(path, args.kubernetes_schema_dir) for path in args.paths)
    print(f"PASS: {count} manifests; YAML keys and bash syntax" +
          ("; cached Kubernetes schema" if args.kubernetes_schema_dir else "; Kubernetes schema NOT checked"))


if __name__ == "__main__":
    main()
