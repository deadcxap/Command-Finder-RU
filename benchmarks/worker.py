"""Isolated benchmark phases using the project's SQLite search implementation."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import resource
import sqlite3
import sys
import time
from types import SimpleNamespace

from benchmarks.metrics import evaluate


_WORKER_STARTED = time.perf_counter()


def _milliseconds(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute Linux path")
    from benchmarks.runner import wsl_path

    return wsl_path(value)


def validate_job(job: object) -> dict:
    if not isinstance(job, dict):
        raise ValueError("job must be an object")
    result = dict(job)
    if not isinstance(job.get("profile"), dict):
        raise ValueError("profile must be an object")
    profile = dict(job["profile"])
    if not isinstance(profile.get("model"), str) or not profile["model"].strip():
        raise ValueError("profile.model must be a nonempty string")
    for key, default in (("revision", "main"), ("query_prefix", ""), ("document_prefix", "")):
        profile.setdefault(key, default)
        if not isinstance(profile[key], str):
            raise ValueError(f"profile.{key} must be a string")
    config_kwargs = profile.get("config_kwargs", {})
    if not isinstance(config_kwargs, dict) or any(not isinstance(key, str) for key in config_kwargs):
        raise ValueError("profile.config_kwargs must be an object with string keys")
    dimensions = profile.get("dimensions")
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
        raise ValueError("profile.dimensions must be a positive integer")
    result["profile"] = profile
    for key in ("profile_name", "corpus"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    if result.get("backend") not in ("project", "sentence-transformers", "onnx"):
        raise ValueError("backend must be project, sentence-transformers, or onnx")
    if result["backend"] == "project":
        if profile["model"] not in ("all-MiniLM-L6-v2", "sentence-transformers/all-MiniLM-L6-v2"):
            raise ValueError("the project backend only supports the unmodified original MiniLM model")
        if dimensions != 384 or profile["query_prefix"] or profile["document_prefix"] or profile["revision"] != "main" or config_kwargs:
            raise ValueError("the project backend requires 384 dimensions, no prefixes, and revision main")
    result["run_dir"] = str(_absolute_path(result.get("run_dir"), "run_dir"))
    result["hf_home"] = str(_absolute_path(result.get("hf_home"), "hf_home"))
    for key, default in (("repeats", 3), ("threads", 4), ("batch_size", 32), ("max_tokens", 128), ("top_k", 7)):
        result.setdefault(key, default)
        if not isinstance(result[key], int) or isinstance(result[key], bool) or result[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if result["top_k"] < 7:
        raise ValueError("top_k must be at least seven for MRR@7")
    result.setdefault("seed", 42)
    if not isinstance(result["seed"], int) or isinstance(result["seed"], bool):
        raise ValueError("seed must be an integer")
    result.setdefault("download", False)
    if not isinstance(result["download"], bool):
        raise ValueError("download must be a boolean")
    result.setdefault("languages", ["ru", "en"])
    languages = result["languages"]
    if not isinstance(languages, list) or not languages or any(x not in ("ru", "en") for x in languages) or len(set(languages)) != len(languages):
        raise ValueError("languages must contain ru and/or en without duplicates")
    cases = result.get("cases")
    evaluate(cases, {})
    if not cases:
        raise ValueError("benchmark phases need at least one query case")
    for case in cases:
        for language in languages:
            text = case.get(f"query_{language}")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{case['id']}: query_{language} must be a nonempty string")
    if result["backend"] == "onnx":
        from benchmarks.runner import onnx_artifact_metadata

        onnx_artifact_metadata(Path(result["run_dir"]) / "onnx", profile, result["max_tokens"])
        result["download"] = False
    elif result["backend"] == "project":
        metadata_path = Path(result["run_dir"]) / "onnx" / "metadata.json"
        if metadata_path.is_file():
            metadata_path = _absolute_path(str(metadata_path), "ONNX metadata")
            artifact = json.loads(metadata_path.read_text(encoding="utf-8"))
            if artifact.get("pooling_in_graph") is True:
                raise ValueError("Use --backend onnx for complete sentence encoders; the original project loader expects token embeddings")
    return result


def _configure_environment(job: dict) -> None:
    run_dir = _absolute_path(job["run_dir"], "run_dir")
    hf_home = _absolute_path(job["hf_home"], "hf_home")
    roots = {
        "HF_HUB_CACHE": hf_home / "hub",
        "TORCH_HOME": hf_home.parent / "torch",
        "XDG_CACHE_HOME": hf_home.parent / "xdg",
    }
    for key, path in roots.items():
        _absolute_path(str(path), key)
    for name in ("cf.db", "seed", "onnx", "embedding-cache"):
        _absolute_path(str(run_dir / name), name)
    run_dir.mkdir(parents=True, exist_ok=True)
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "CF_MODEL": job["profile"]["model"],
        "CF_EMBEDDING_DIM": str(job["profile"]["dimensions"]),
        "CF_DB_DIR": str(run_dir),
        "CF_DB_NAME": "cf.db",
        "CF_DATA_DIR": str(run_dir / "seed"),
        "HF_HOME": str(hf_home),
        **{key: str(path) for key, path in roots.items()},
        "SENTENCE_TRANSFORMERS_HOME": str(hf_home),
        "HF_HUB_OFFLINE": "0" if job["download"] else "1",
        "TRANSFORMERS_OFFLINE": "0" if job["download"] else "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "CUDA_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": str(job["threads"]),
        "MKL_NUM_THREADS": str(job["threads"]),
        "OPENBLAS_NUM_THREADS": str(job["threads"]),
    })


def _versions() -> dict:
    versions = {}
    for name in ("numpy", "sqlite-vec", "sentence-transformers", "transformers", "torch", "onnxruntime", "tokenizers"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    versions["python"] = sys.version.split()[0]
    return versions


def _torch_threads(count: int):
    import torch

    torch.set_num_threads(count)
    torch.set_num_interop_threads(1)
    return torch


def _model_metadata(model, job: dict) -> dict:
    transformer = model[0].auto_model
    config = transformer.config
    revision = getattr(config, "_commit_hash", None)
    return {
        "model": job["profile"]["model"],
        "requested_revision": job["profile"]["revision"],
        "resolved_revision": revision,
        "revision_verified": bool(revision),
        "dimensions": int(model.get_sentence_embedding_dimension()),
        "dtype": str(transformer.dtype).removeprefix("torch."),
        "encoder_backend": "pytorch",
        "device": str(getattr(transformer, "device", getattr(model, "device", "unknown"))),
        "effective_max_tokens": int(model.max_seq_length),
        "effective_threads": job["threads"],
        "effective_batch_size": job["batch_size"],
        "query_prefix": job["profile"]["query_prefix"],
        "document_prefix": job["profile"]["document_prefix"],
        "config_kwargs": job["profile"].get("config_kwargs", {}),
        "reference_compile": getattr(config, "reference_compile", None),
    }


class SentenceTransformerEncoder:
    def __init__(self, model, job: dict, metadata: dict, *, import_ms: float = 0.0, load_ms: float = 0.0):
        self.model = model
        self.job = job
        self.metadata = metadata
        self.import_ms = import_ms
        self.load_ms = load_ms

    def _array(self, value, count: int | None = None):
        import numpy as np

        array = np.asarray(value, dtype=np.float32)
        shape = (self.job["profile"]["dimensions"],) if count is None else (count, self.job["profile"]["dimensions"])
        if array.shape != shape or not np.all(np.isfinite(array)):
            raise ValueError(f"encoder returned invalid vectors: expected {shape}, got {array.shape}")
        return array

    def encode_query(self, text: str):
        return self._array(self.model.encode(
            text,
            prompt=self.job["profile"]["query_prefix"],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ))

    def encode_batch(self, texts: list[str], batch_size: int | None = None):
        import numpy as np

        fingerprint = {
            "profile": self.job["profile"],
            "resolved_revision": self.metadata.get("resolved_revision"),
            "max_tokens": self.job["max_tokens"],
            "texts": texts,
        }
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cache = Path(self.job["run_dir"]) / "embedding-cache" / f"documents-{digest}.npy"
        if cache.is_file():
            return self._array(np.load(cache, allow_pickle=False), len(texts))
        vectors = self._array(self.model.encode(
            texts,
            batch_size=batch_size or self.job["batch_size"],
            prompt=self.job["profile"]["document_prefix"],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ), len(texts))
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, vectors, allow_pickle=False)
        return vectors


def _create_encoder(job: dict, embeddings):
    imported = time.perf_counter()
    if job["backend"] == "onnx":
        import numpy
        import onnxruntime
        import tokenizers
        from benchmarks.onnx_encoder import OnnxEncoder
        from benchmarks.runner import onnx_artifact_metadata

        import_ms = _milliseconds(imported)
        loading = time.perf_counter()
        directory = Path(job["run_dir"]) / "onnx"
        artifact = onnx_artifact_metadata(directory, job["profile"], job["max_tokens"])
        encoder = OnnxEncoder(directory, threads=job["threads"], batch_size=job["batch_size"])
        encoder.import_ms = import_ms
        encoder.load_ms = _milliseconds(loading)
        encoder.metadata = {
            "model": artifact["model"],
            "requested_revision": job["profile"]["revision"],
            "resolved_revision": artifact["revision"],
            "revision_verified": True,
            "dimensions": artifact["dimensions"],
            "dtype": "float32",
            "weight_precision": artifact["precision"],
            "encoder_backend": "onnx",
            "device": "cpu",
            "effective_max_tokens": artifact["max_tokens"],
            "effective_threads": job["threads"],
            "effective_batch_size": job["batch_size"],
            "query_prefix": artifact["query_prefix"],
            "document_prefix": artifact["document_prefix"],
            "onnx_bytes": (directory / artifact.get("model_file", "model.onnx")).stat().st_size,
            "artifact_metadata": artifact,
            **encoder.metadata,
        }
        return encoder
    if job["backend"] == "project" and embeddings._onnx_available():
        if job["threads"] != 4:
            raise ValueError("the original ONNX backend fixes four threads; use --threads 4 for a comparable run")
        import_ms = _milliseconds(imported)
        loading = time.perf_counter()
        session, _ = embeddings._get_onnx()
        path = Path(job["run_dir"]) / "onnx" / "model.onnx"
        metadata = {
            "model": job["profile"]["model"],
            "requested_revision": "main",
            "resolved_revision": None,
            "revision_verified": False,
            "dimensions": job["profile"]["dimensions"],
            "dtype": session.get_outputs()[0].type,
            "encoder_backend": "onnx",
            "device": "cpu",
            "effective_max_tokens": 128,
            "effective_threads": 4,
            "effective_batch_size": 128,
            "onnx_bytes": path.stat().st_size,
            "query_prefix": "",
            "document_prefix": "",
        }
        return SimpleNamespace(metadata=metadata, import_ms=import_ms, load_ms=_milliseconds(loading))

    _torch_threads(job["threads"])
    if job["backend"] == "project":
        import_ms = _milliseconds(imported)
        loading = time.perf_counter()
        model = embeddings._get_full_model()
        model.to("cpu")
        load_ms = _milliseconds(loading)
        metadata = _model_metadata(model, job)
        metadata["effective_batch_size"] = 128
        return SimpleNamespace(metadata=metadata, import_ms=import_ms, load_ms=load_ms)

    from sentence_transformers import SentenceTransformer

    import_ms = _milliseconds(imported)
    loading = time.perf_counter()
    model = SentenceTransformer(
        job["profile"]["model"],
        revision=job["profile"]["revision"],
        device="cpu",
        cache_folder=job["hf_home"],
        local_files_only=not job["download"],
        trust_remote_code=False,
        config_kwargs=job["profile"].get("config_kwargs", {}),
    )
    dimension = model.get_sentence_embedding_dimension()
    if dimension != job["profile"]["dimensions"]:
        raise ValueError(f"model produces {dimension} dimensions, profile requests {job['profile']['dimensions']}")
    model.max_seq_length = job["max_tokens"]
    metadata = _model_metadata(model, job)
    return SentenceTransformerEncoder(model, job, metadata, import_ms=import_ms, load_ms=_milliseconds(loading))


def _build_runtime(job: dict, encoder_factory=None):
    _configure_environment(job)
    imported = time.perf_counter()
    from benchmarks.legacy import db, embeddings, search, seed

    expected_db = Path(job["run_dir"]) / "cf.db"
    if db.DB_PATH != expected_db or db.EMBEDDING_DIM != job["profile"]["dimensions"]:
        raise RuntimeError("Legacy benchmark modules were imported before the benchmark environment; start a fresh worker process")
    embeddings.CACHE_DIR = Path(job["run_dir"]) / "embedding-cache"
    import_ms = _milliseconds(imported)
    encoder = (encoder_factory or _create_encoder)(job, embeddings)
    if job["backend"] in ("sentence-transformers", "onnx"):
        embeddings.encode_text = encoder.encode_query
        embeddings.encode_batch = encoder.encode_batch
        seed.encode_batch = encoder.encode_batch
    return SimpleNamespace(
        db=db, embeddings=embeddings, search=search, seed=seed, encoder=encoder,
        import_ms=import_ms + encoder.import_ms,
        model_load_ms=encoder.load_ms,
    )


def _clear_query_cache(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM query_cache")
        connection.commit()


def _search(runtime, job: dict, query: str) -> list[str]:
    results = runtime.search.search(query, top_k=job["top_k"], db_path=Path(job["run_dir"]) / "cf.db")
    return [row.command_template for row in results]


def _latencies(samples: list[float]) -> dict:
    ordered = sorted(samples)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        offset = (len(ordered) - 1) * fraction
        lower = int(offset)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower)

    return {
        "count": len(ordered),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "mean": sum(ordered) / len(ordered) if ordered else None,
    }


def _database_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")) if p.is_file())


def _prepare(runtime, job: dict) -> dict:
    started = time.perf_counter()
    stats = runtime.seed.seed_database(db_path=Path(job["run_dir"]) / "cf.db", force=True)
    elapsed = _milliseconds(started)
    if runtime.encoder.metadata.get("encoder_backend") == "onnx":
        digest = hashlib.sha256()
        filename = runtime.encoder.metadata.get("model_file", "model.onnx")
        with (Path(job["run_dir"]) / "onnx" / filename).open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        runtime.encoder.metadata["onnx_sha256"] = digest.hexdigest()
    return {
        "imports_ms": runtime.import_ms,
        "model_load_ms": runtime.model_load_ms,
        "index_build_ms": elapsed,
        "stats": stats,
        "db_bytes": _database_bytes(Path(job["run_dir"]) / "cf.db"),
        "vector_bytes": stats["patterns"] * job["profile"]["dimensions"] * 4,
    }


def _first_case(job: dict) -> dict:
    return next((case for case in job["cases"] if case["expected_commands"]), job["cases"][0])


def _cold(runtime, job: dict, language: str, started: float) -> dict:
    case = _first_case(job)
    _clear_query_cache(Path(job["run_dir"]) / "cf.db")
    ranking = _search(runtime, job, case[f"query_{language}"])
    return {
        "language": language,
        "query_id": case["id"],
        "ranking": ranking,
        "startup_and_first_query_ms": _milliseconds(started),
    }


def _cached_cold(job: dict, language: str, started: float) -> dict:
    _configure_environment(job)
    from benchmarks.legacy import db, search

    path = Path(job["run_dir"]) / "cf.db"
    if db.DB_PATH != path or db.EMBEDDING_DIM != job["profile"]["dimensions"]:
        raise RuntimeError("Legacy benchmark modules were imported before the benchmark environment; start a fresh worker process")
    case = _first_case(job)
    query = case[f"query_{language}"]
    cache_key = search._normalize_query(query)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT embedding FROM query_cache WHERE query_text = ?", (cache_key,)).fetchone()
    if row is None:
        raise ValueError("cached-cold requires the first query to be cached; run cold for this language immediately before it")
    if not isinstance(row[0], bytes) or len(row[0]) != job["profile"]["dimensions"] * 4:
        raise ValueError("cached-cold found an invalid cached vector")
    results = search.search(query, top_k=job["top_k"], db_path=path)
    return {
        "language": language,
        "query_id": case["id"],
        "ranking": [result.command_template for result in results],
        "startup_and_cached_query_ms": _milliseconds(started),
        "model": {
            "model": job["profile"]["model"],
            "dimensions": job["profile"]["dimensions"],
            "encoder_backend": "query-cache",
            "model_loaded": False,
        },
    }


def _warm_language(runtime, job: dict, language: str) -> dict:
    path = Path(job["run_dir"]) / "cf.db"
    cases = job["cases"]
    _clear_query_cache(path)
    _search(runtime, job, cases[0][f"query_{language}"])
    generator = random.Random(job["seed"])
    rankings = {}
    uncached_samples = []
    cache_hit_samples = []
    repeat_mismatches = []
    for repeat in range(job["repeats"]):
        order = list(cases)
        generator.shuffle(order)
        for case in order:
            query = case[f"query_{language}"]
            _clear_query_cache(path)
            started = time.perf_counter()
            ranking = _search(runtime, job, query)
            uncached_samples.append(_milliseconds(started))
            if repeat == 0:
                rankings[case["id"]] = ranking
            elif ranking != rankings[case["id"]]:
                repeat_mismatches.append({"id": case["id"], "repeat": repeat + 1, "ranking": ranking})
            _clear_query_cache(path)
            _search(runtime, job, query)
            started = time.perf_counter()
            _search(runtime, job, query)
            cache_hit_samples.append(_milliseconds(started))

    _clear_query_cache(path)
    session_mismatches = []
    for case in cases:
        ranking = _search(runtime, job, case[f"query_{language}"])
        uncached = rankings[case["id"]]
        if ranking != uncached:
            session_mismatches.append({
                "id": case["id"],
                "cache_key": runtime.search._normalize_query(case[f"query_{language}"]),
                "uncached": uncached,
                "session": ranking,
                "top1_changed": ranking[:1] != uncached[:1],
            })
    return {
        "metrics": evaluate(cases, rankings),
        "rankings": rankings,
        "warmup_query_count": 1,
        "latency_ms": {"uncached": _latencies(uncached_samples), "cache_hit": _latencies(cache_hit_samples)},
        "repeat_ranking_mismatches": repeat_mismatches,
        "session_order": [case["id"] for case in cases],
        "session_cache_mismatches": session_mismatches,
    }


def run_phase(job: dict, phase: str, language: str | None = None, *, started: float | None = None, encoder_factory=None) -> dict:
    started = time.perf_counter() if started is None else started
    job = validate_job(job)
    if phase not in ("prepare", "cold", "cached-cold", "warm"):
        raise ValueError("phase must be prepare, cold, cached-cold, or warm")
    if phase in ("cold", "cached-cold") and language not in job["languages"]:
        raise ValueError("cold query language must be selected in the job")
    if phase != "prepare":
        job["download"] = False
    path = _absolute_path(str(Path(job["run_dir"]) / "cf.db"), "benchmark database")
    if phase != "prepare" and not path.is_file():
        raise ValueError("prepare must build the benchmark database before cold or warm phases")
    runtime = None if phase == "cached-cold" else _build_runtime(job, encoder_factory=encoder_factory)
    if phase == "cached-cold":
        result = _cached_cold(job, language, started)
        filename = f"cached-cold-{language}.json"
        model_metadata = result["model"]
    elif phase == "prepare":
        result = _prepare(runtime, job)
        filename = "prepare.json"
    elif phase == "cold":
        result = _cold(runtime, job, language, started)
        filename = f"cold-{language}.json"
    else:
        result = {"languages": {language: _warm_language(runtime, job, language) for language in job["languages"]}}
        filename = "warm.json"
    if runtime is not None:
        model_metadata = runtime.encoder.metadata
    result.update({
        "phase": phase,
        "profile_name": job["profile_name"],
        "backend": job["backend"],
        "corpus": job["corpus"],
        "model": model_metadata,
        "library_versions": _versions(),
        "peak_rss_bytes": _peak_rss_bytes(),
        "download_allowed": job["download"],
    })
    output = _absolute_path(str(Path(job["run_dir"]) / filename), "phase output")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--phase", required=True, choices=("prepare", "cold", "cached-cold", "warm"))
    parser.add_argument("--language", choices=("ru", "en"))
    args = parser.parse_args(argv)
    try:
        job_path = _absolute_path(args.job, "job")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        result = run_phase(job, args.phase, args.language, started=_WORKER_STARTED)
    except Exception as exc:
        print(f"benchmark worker: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"phase": result["phase"], "profile_name": result["profile_name"], "peak_rss_bytes": result["peak_rss_bytes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
