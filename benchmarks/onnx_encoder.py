"""CPU sentence embeddings from a self-contained exported ONNX artifact."""

import json
from pathlib import Path

from benchmarks.runner import wsl_path


def _positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("ONNX metadata must be an object")
    result = dict(value)
    for name in ("dimensions", "max_tokens"):
        result[name] = _positive_integer(result.get(name), name)
    for name in ("query_prefix", "document_prefix"):
        result.setdefault(name, "")
        if not isinstance(result[name], str):
            raise ValueError(f"{name} must be a string")
    for name, default in (("do_lower_case", False), ("strip_text", True)):
        result.setdefault(name, default)
        if not isinstance(result[name], bool):
            raise ValueError(f"{name} must be a boolean")
    for name in ("padding_side", "truncation_side"):
        result.setdefault(name, "right")
        if result[name] not in ("left", "right"):
            raise ValueError(f"{name} must be left or right")
    pad_id = result.get("pad_token_id")
    if not isinstance(pad_id, int) or isinstance(pad_id, bool) or pad_id < 0:
        raise ValueError("pad_token_id must be a nonnegative integer")
    if not isinstance(result.get("pad_token"), str) or not result["pad_token"]:
        raise ValueError("pad_token must be a nonempty string")
    names = result.get("input_names")
    supported = {"input_ids", "attention_mask", "token_type_ids"}
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or name not in supported for name in names)
            or len(set(names)) != len(names) or "input_ids" not in names):
        raise ValueError("input_names must contain distinct supported tokenizer inputs")
    result.setdefault("output_name", "sentence_embedding")
    if not isinstance(result["output_name"], str) or not result["output_name"]:
        raise ValueError("output_name must be a nonempty string")
    result.setdefault("model_file", "model.onnx")
    name = result["model_file"]
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("model_file must be a filename inside the artifact directory")
    return result


class OnnxEncoder:
    def __init__(self, directory: str | Path, threads: int = 2, batch_size: int = 32):
        self.directory = wsl_path(directory)
        self.threads = _positive_integer(threads, "threads")
        self.batch_size = _positive_integer(batch_size, "batch_size")
        metadata_path = wsl_path(self.directory / "metadata.json")
        tokenizer_path = wsl_path(self.directory / "tokenizer.json")
        self.metadata = _metadata(json.loads(metadata_path.read_text(encoding="utf-8")))
        model_path = wsl_path(self.directory / self.metadata["model_file"])
        self.dimensions = self.metadata["dimensions"]
        self.max_tokens = self.metadata["max_tokens"]

        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(self.max_tokens, direction=self.metadata["truncation_side"])
        self.tokenizer.enable_padding(
            direction=self.metadata["padding_side"],
            pad_id=self.metadata["pad_token_id"],
            pad_token=self.metadata["pad_token"],
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        if {item.name for item in inputs} != set(self.metadata["input_names"]):
            raise ValueError("ONNX graph inputs do not match metadata.input_names")
        dtypes = {"tensor(int64)": np.int64, "tensor(int32)": np.int32}
        if any(item.type not in dtypes for item in inputs):
            raise ValueError("ONNX tokenizer inputs must be int64 or int32 tensors")
        self._input_dtypes = {item.name: dtypes[item.type] for item in inputs}
        if self.metadata["output_name"] not in {item.name for item in self.session.get_outputs()}:
            raise ValueError("ONNX graph does not contain metadata.output_name")

    def _text(self, text: str, prefix: str) -> str:
        value = prefix + text
        if self.metadata["strip_text"]:
            value = value.strip()
        if self.metadata["do_lower_case"]:
            value = value.lower()
        return value

    def _encode(self, texts: list[str], prefix: str, batch_size: int):
        np = self._np
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        parts = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            tokens = self.tokenizer.encode_batch([self._text(text, prefix) for text in batch])
            values = {
                "input_ids": [token.ids for token in tokens],
                "attention_mask": [token.attention_mask for token in tokens],
                "token_type_ids": [token.type_ids for token in tokens],
            }
            feed = {name: np.asarray(values[name], dtype=dtype) for name, dtype in self._input_dtypes.items()}
            output = self.session.run([self.metadata["output_name"]], feed)[0]
            vectors = np.asarray(output, dtype=np.float32)
            expected = (len(batch), self.dimensions)
            if vectors.shape != expected or not np.all(np.isfinite(vectors)):
                raise ValueError(f"ONNX returned invalid vectors: expected {expected}, got {vectors.shape}")
            parts.append(vectors)
        return np.concatenate(parts, axis=0)

    def encode_query(self, text: str):
        if not isinstance(text, str):
            raise ValueError("query must be a string")
        return self._encode([text], self.metadata["query_prefix"], 1)[0]

    def encode_batch(self, texts: list[str], batch_size: int | None = None):
        if not isinstance(texts, (list, tuple)) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a list of strings")
        count = self.batch_size if batch_size is None else _positive_integer(batch_size, "batch_size")
        return self._encode(list(texts), self.metadata["document_prefix"], count)
