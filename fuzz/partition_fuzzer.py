"""Check that weighted CI partitioning never drops or duplicates a check."""

import importlib.util
import math
import sys
from pathlib import Path

import atheris

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci_partitioning.py"
with atheris.instrument_imports():
    spec = importlib.util.spec_from_file_location("ci_partitioning", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    atheris.instrument_func(module.partition_names)
    atheris.instrument_func(module.partition_load)


@atheris.instrument_func
def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    count = provider.ConsumeIntInRange(1, 32)
    names = [f"{provider.ConsumeUnicodeNoSurrogates(12)}-{i}" for i in range(count)]
    partitions = provider.ConsumeIntInRange(1, count)
    weights = {name: provider.ConsumeIntInRange(1, 1000) / 10 for name in names}
    result = module.partition_names(names, partitions, weights=weights)
    assert len(result) == partitions
    assert all(result)
    assert sorted(name for group in result for name in group) == sorted(names)
    assert result == module.partition_names(names[::-1], partitions, weights=weights)
    loads = [module.partition_load(group, weights=weights) for group in result]
    assert math.isclose(sum(loads), sum(weights.values()), rel_tol=1e-12)
    assert max(loads) - min(loads) <= max(weights.values()) + 1e-9


if __name__ == "__main__":
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
