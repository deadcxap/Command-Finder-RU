import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from cf import embeddings
from cf.config import EMBEDDING_DIM


@pytest.fixture
def encoder_artifact(tmp_path, monkeypatch):
    import onnx
    from onnx import TensorProto, helper
    from tokenizers import Tokenizer, models, pre_tokenizers, processors

    directory = tmp_path / "model"
    directory.mkdir()
    tokenizer = Tokenizer(models.WordLevel({
        "[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3,
        "русский": 4, "текст": 5, "hello": 6, "мир": 7,
    }, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    tokenizer.save(str(directory / "tokenizer.json"))
    inputs = [helper.make_tensor_value_info(name, TensorProto.INT64, ["batch", "sequence"])
              for name in ("input_ids", "attention_mask")]
    output = helper.make_tensor_value_info("sentence_embedding", TensorProto.FLOAT, ["batch", EMBEDDING_DIM])
    weights = np.zeros((2, EMBEDDING_DIM), dtype=np.float32)
    weights[0, 0] = weights[1, 1] = 1
    initializers = [helper.make_tensor("axes", TensorProto.INT64, [1], [1]),
                    helper.make_tensor("one", TensorProto.FLOAT, [1], [1.0]),
                    helper.make_tensor("weights", TensorProto.FLOAT, list(weights.shape), weights.flatten())]
    nodes = [
        helper.make_node("Cast", ["input_ids"], ["ids"], to=TensorProto.FLOAT),
        helper.make_node("Cast", ["attention_mask"], ["mask"], to=TensorProto.FLOAT),
        helper.make_node("Mul", ["ids", "mask"], ["masked"]),
        helper.make_node("ReduceSum", ["masked", "axes"], ["total"], keepdims=1),
        helper.make_node("ReduceSum", ["mask", "axes"], ["count"], keepdims=1),
        helper.make_node("Div", ["total", "count"], ["mean"]),
        helper.make_node("Add", ["mean", "one"], ["offset"]),
        helper.make_node("Concat", ["mean", "offset"], ["features"], axis=1),
        helper.make_node("ReduceL2", ["features"], ["norm"], axes=[1], keepdims=1),
        helper.make_node("Div", ["features", "norm"], ["normalized"]),
        helper.make_node("MatMul", ["normalized", "weights"], ["sentence_embedding"]),
    ]
    graph = helper.make_graph(nodes, "test-encoder", inputs, [output], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
    onnx.save(model, directory / "model.onnx")
    metadata = {
        "schema_version": 1, "model": embeddings.MODEL_NAME, "revision": embeddings.MODEL_REVISION,
        "dimensions": EMBEDDING_DIM, "max_tokens": 6, "precision": "int8",
        "query_prefix": "", "document_prefix": "", "normalization": "l2", "pooling_in_graph": True,
        "input_names": ["input_ids", "attention_mask"], "output_name": "sentence_embedding",
        "pad_token_id": 0, "pad_token": "[PAD]", "padding_side": "right", "truncation_side": "right",
        "strip_text": True, "do_lower_case": False,
        "quantization": embeddings.MODEL_SPEC["quantization"],
    }
    (directory / "metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(embeddings, "ONNX_DIR", directory)
    monkeypatch.setattr(embeddings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(embeddings, "MAX_TOKENS", 6)
    for name in ("_encoder", "_encoder_fingerprint", "_fingerprint_stamp", "_fingerprint_value"):
        monkeypatch.setattr(embeddings, name, None)
    return directory, metadata


def test_float32_bytes_roundtrip_and_contiguity():
    vector = np.random.default_rng(42).standard_normal(EMBEDDING_DIM * 2)[::2]
    value = embeddings.to_bytes(vector)
    assert len(value) == EMBEDDING_DIM * 4
    np.testing.assert_array_equal(np.frombuffer(value, dtype=np.float32), vector.astype(np.float32))


def test_missing_model_has_setup_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "ONNX_DIR", tmp_path / "absent")
    assert not embeddings._onnx_available()
    with pytest.raises(RuntimeError, match="cf --prepare-model"):
        embeddings.encode_text("найди файл")


def test_empty_batch_does_not_require_a_model(monkeypatch):
    monkeypatch.setattr(embeddings, "_get_encoder", lambda: pytest.fail("model must not load"))
    actual = embeddings.encode_batch([])
    assert actual.shape == (0, EMBEDDING_DIM)
    assert actual.dtype == np.float32


def test_real_runtime_handles_unicode_dynamic_padding_and_empty_text(encoder_artifact):
    assert embeddings._onnx_available()
    texts = ["русский", "hello мир текст", "", "  русский  "]
    batched = embeddings.encode_batch(texts, batch_size=3)
    individual = np.stack([embeddings.encode_text(text) for text in texts])
    assert batched.shape == (4, EMBEDDING_DIM)
    assert batched.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(batched, axis=1), 1, atol=1e-6)
    np.testing.assert_allclose(batched, individual, atol=1e-6)
    np.testing.assert_array_equal(batched[0], batched[3])


def test_batch_size_and_truncation_are_enforced(encoder_artifact, monkeypatch):
    encoder = embeddings._get_encoder()
    original = encoder.session.run
    shapes = []

    def run(outputs, inputs):
        shapes.append(inputs["input_ids"].shape)
        return original(outputs, inputs)

    monkeypatch.setattr(encoder.session, "run", run)
    embeddings.encode_batch(["русский " * 100] * 5, batch_size=2)
    assert shapes == [(2, 6), (2, 6), (1, 6)]


def test_staged_directory_validation_does_not_use_the_global_model_path(encoder_artifact, monkeypatch):
    directory, _ = encoder_artifact
    monkeypatch.setattr(embeddings, "ONNX_DIR", directory.parent / "not-installed")
    encoder = embeddings.OnnxEncoder(directory, threads=1, batch_size=2)
    assert encoder.encode_query("русский").shape == (EMBEDDING_DIM,)
    assert encoder.encode_batch(["русский", "hello"]).shape == (2, EMBEDDING_DIM)
    assert not embeddings._onnx_available()


@pytest.mark.parametrize("output", [np.full((1, EMBEDDING_DIM), np.nan, dtype=np.float32),
                                   np.zeros((1, EMBEDDING_DIM), dtype=np.float32),
                                   np.ones((1, EMBEDDING_DIM + 1), dtype=np.float32)])
def test_invalid_model_vectors_are_rejected(encoder_artifact, monkeypatch, output):
    encoder = embeddings._get_encoder()
    monkeypatch.setattr(encoder.session, "run", lambda *args: [output])
    with pytest.raises(ValueError):
        embeddings.encode_text("русский")


def test_cached_batch_does_not_construct_an_onnx_session(encoder_artifact, monkeypatch):
    texts = ["русский текст", "hello"]
    expected = embeddings.encode_batch(texts, batch_size=2)
    monkeypatch.setattr(embeddings, "_encoder", None)
    monkeypatch.setattr(embeddings, "_get_encoder", lambda: pytest.fail("cache hit must not load the model"))
    np.testing.assert_array_equal(embeddings.encode_batch(texts, batch_size=2), expected)


def test_cache_key_distinguishes_order_newlines_and_batch_size(encoder_artifact):
    first = embeddings._cache_path(["a\nb", "c"], 2)
    assert first != embeddings._cache_path(["a", "b\nc"], 2)
    assert first != embeddings._cache_path(["c", "a\nb"], 2)
    assert first != embeddings._cache_path(["a\nb", "c"], 1)
    assert first.parent == embeddings.CACHE_DIR
    assert first.suffix == ".npy"


def test_model_content_change_invalidates_cached_vectors(encoder_artifact):
    import onnx
    directory, _ = encoder_artifact
    texts = ["русский"]
    before = embeddings.encode_batch(texts)
    cache_before = embeddings._cache_path(texts)
    model = onnx.load(directory / "model.onnx")
    next(value for value in model.graph.initializer if value.name == "one").float_data[0] = 2.0
    onnx.save(model, directory / "model.onnx")
    cache_after = embeddings._cache_path(texts)
    after = embeddings.encode_batch(texts)
    assert cache_before != cache_after
    assert cache_before.is_file() and cache_after.is_file()
    assert not np.allclose(before, after)


def test_metadata_change_invalidates_cached_vectors(encoder_artifact):
    directory, metadata = encoder_artifact
    texts = ["HELLO"]
    before = embeddings.encode_batch(texts)
    cache_before = embeddings._cache_path(texts)
    metadata["do_lower_case"] = True
    (directory / "metadata.json").write_text(json.dumps(metadata))
    after = embeddings.encode_batch(texts)
    assert cache_before != embeddings._cache_path(texts)
    assert not np.allclose(before, after)


def test_corrupted_embedding_cache_is_regenerated(encoder_artifact):
    texts = ["русский"]
    expected = embeddings.encode_batch(texts)
    cache = embeddings._cache_path(texts)
    np.save(cache, np.zeros((1, EMBEDDING_DIM), dtype=np.float32))
    np.testing.assert_array_equal(embeddings.encode_batch(texts), expected)
    np.testing.assert_array_equal(np.load(cache, allow_pickle=False), expected)


@pytest.mark.parametrize("field,value", [("dimensions", 384), ("revision", "different"),
                                         ("pooling_in_graph", False), ("precision", "fp32")])
def test_incompatible_model_is_rejected(encoder_artifact, field, value):
    directory, metadata = encoder_artifact
    metadata[field] = value
    (directory / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="не соответствуют"):
        embeddings.encode_text("русский")


def test_wrong_quantization_scheme_is_rejected(encoder_artifact):
    directory, metadata = encoder_artifact
    metadata["quantization"] = {**metadata["quantization"], "weight_type": "QInt8"}
    (directory / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="U8U8"):
        embeddings.encode_text("русский")


def test_runtime_imports_do_not_load_training_libraries():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import cf.embeddings; assert not {'torch', 'transformers', 'sentence_transformers'} & sys.modules.keys()"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
