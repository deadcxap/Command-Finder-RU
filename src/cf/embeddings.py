import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from cf.config import (
    DB_DIR,
    DEFAULT_BATCH_SIZE,
    EMBEDDING_DIM,
    MAX_TOKENS,
    MODEL_NAME,
    MODEL_PRECISION,
    MODEL_REVISION,
    MODEL_SPEC,
    NUM_THREADS,
    ONNX_DIR,
)


CACHE_DIR = DB_DIR / "cache"
_encoder = None
_encoder_fingerprint = None
_fingerprint_stamp = None
_fingerprint_value = None
_ASSETS = ("model.onnx", "tokenizer.json", "metadata.json")


def _positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name}: требуется положительное целое число")
    return value


def _onnx_available() -> bool:
    return all((ONNX_DIR / name).is_file() for name in _ASSETS)


def _missing_model(directory: Path | None = None) -> RuntimeError:
    return RuntimeError(
        f"Модель не подготовлена в {directory or ONNX_DIR}. Выполните cf --prepare-model, затем cf --seed."
    )


def model_fingerprint() -> str:
    global _fingerprint_stamp, _fingerprint_value
    paths = [ONNX_DIR / name for name in _ASSETS]
    try:
        stamp = tuple((str(path.absolute()), path.stat().st_size, path.stat().st_mtime_ns,
                       path.stat().st_ctime_ns, path.stat().st_ino) for path in paths)
    except FileNotFoundError:
        raise _missing_model() from None
    identity = (MODEL_NAME, MODEL_REVISION, EMBEDDING_DIM, MAX_TOKENS, MODEL_PRECISION, NUM_THREADS)
    stamp = (identity, stamp)
    if stamp != _fingerprint_stamp:
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode())
        for path in paths:
            digest.update(path.name.encode())
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        _fingerprint_stamp, _fingerprint_value = stamp, digest.hexdigest()
    return _fingerprint_value


def _load_metadata(directory: Path) -> dict:
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _missing_model(directory) from None
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("Повреждён файл metadata.json модели; подготовьте модель заново") from error
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("Неподдерживаемый формат метаданных модели")
    expected = {"model": MODEL_NAME, "revision": MODEL_REVISION, "dimensions": EMBEDDING_DIM,
                "max_tokens": MAX_TOKENS, "precision": MODEL_PRECISION,
                "query_prefix": "", "document_prefix": "", "normalization": "l2",
                "pooling_in_graph": True, "output_name": "sentence_embedding"}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("Модель, её размерность или формат не соответствуют настройкам cf; подготовьте модель заново")
    quantization = metadata.get("quantization")
    if not isinstance(quantization, dict) or any(quantization.get(key) != value for key, value in MODEL_SPEC["quantization"].items()):
        raise ValueError("Схема квантования модели не соответствует рабочему варианту U8U8")
    if metadata.get("model_file", "model.onnx") != "model.onnx":
        raise ValueError("Граф модели должен называться model.onnx")
    names = metadata.get("input_names")
    supported = {"input_ids", "attention_mask", "token_type_ids"}
    if (not isinstance(names, list) or any(not isinstance(name, str) or name not in supported for name in names)
            or not {"input_ids", "attention_mask"}.issubset(names) or len(names) != len(set(names))):
        raise ValueError("Метаданные содержат неподдерживаемые входы модели")
    for name in ("padding_side", "truncation_side"):
        if metadata.get(name, "right") not in ("left", "right"):
            raise ValueError(f"Неверное значение {name} в метаданных модели")
    for name, default in (("do_lower_case", False), ("strip_text", True)):
        if not isinstance(metadata.get(name, default), bool):
            raise ValueError(f"Неверное значение {name} в метаданных модели")
    pad_id = metadata.get("pad_token_id")
    if not isinstance(pad_id, int) or isinstance(pad_id, bool) or pad_id < 0:
        raise ValueError("Неверный идентификатор токена заполнения")
    if not isinstance(metadata.get("pad_token"), str) or not metadata["pad_token"]:
        raise ValueError("Не указан токен заполнения")
    return metadata


def _vectors(value, count: int) -> np.ndarray:
    vectors = np.asarray(value, dtype=np.float32)
    expected = (count, EMBEDDING_DIM)
    if vectors.shape != expected or not np.isfinite(vectors).all():
        raise ValueError(f"Неверные векторы модели: ожидается форма {expected}, получена {vectors.shape}")
    if count and not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=2e-5):
        raise ValueError("Модель вернула ненормализованные векторы")
    return vectors


class OnnxEncoder:
    def __init__(self, directory: str | Path = ONNX_DIR, threads: int = NUM_THREADS,
                 batch_size: int = DEFAULT_BATCH_SIZE):
        from tokenizers import Tokenizer
        import onnxruntime as ort

        self.directory = Path(directory)
        self.threads = _positive_integer(threads, "CF_THREADS")
        self.batch_size = _positive_integer(batch_size, "Размер пакета")
        self.metadata = _load_metadata(self.directory)
        self.tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS, direction=self.metadata.get("truncation_side", "right"))
        self.tokenizer.enable_padding(
            direction=self.metadata.get("padding_side", "right"),
            pad_id=self.metadata["pad_token_id"], pad_token=self.metadata["pad_token"],
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(self.directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        if {item.name for item in inputs} != set(self.metadata["input_names"]):
            raise ValueError("Входы ONNX-графа не совпадают с метаданными модели")
        dtypes = {"tensor(int64)": np.int64, "tensor(int32)": np.int32}
        if any(item.type not in dtypes for item in inputs):
            raise ValueError("Входы ONNX-графа должны содержать целые токены")
        self.input_dtypes = {item.name: dtypes[item.type] for item in inputs}
        outputs = {item.name: item for item in self.session.get_outputs()}
        output = outputs.get("sentence_embedding")
        if output is None or output.type != "tensor(float)" or len(output.shape) != 2:
            raise ValueError("ONNX-граф должен возвращать sentence_embedding в формате float32")

    def encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        parts = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            if self.metadata.get("strip_text", True):
                batch = [text.strip() for text in batch]
            if self.metadata.get("do_lower_case", False):
                batch = [text.lower() for text in batch]
            tokens = self.tokenizer.encode_batch(batch)
            values = {"input_ids": [token.ids for token in tokens],
                      "attention_mask": [token.attention_mask for token in tokens],
                      "token_type_ids": [token.type_ids for token in tokens]}
            feed = {name: np.asarray(values[name], dtype=dtype) for name, dtype in self.input_dtypes.items()}
            output = self.session.run(["sentence_embedding"], feed)[0]
            parts.append(_vectors(output, len(batch)))
        return np.concatenate(parts, axis=0) if parts else np.empty((0, EMBEDDING_DIM), dtype=np.float32)

    def encode_query(self, text: str) -> np.ndarray:
        if not isinstance(text, str):
            raise ValueError("Запрос должен быть строкой")
        return self.encode([text], 1)[0]

    def encode_batch(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        if not isinstance(texts, (list, tuple)) or any(not isinstance(text, str) for text in texts):
            raise ValueError("Описания должны быть списком строк")
        count = self.batch_size if batch_size is None else _positive_integer(batch_size, "Размер пакета")
        return self.encode(list(texts), count)


def _get_encoder() -> OnnxEncoder:
    global _encoder, _encoder_fingerprint
    fingerprint = model_fingerprint()
    if _encoder is None or fingerprint != _encoder_fingerprint:
        _encoder = OnnxEncoder(ONNX_DIR, NUM_THREADS, DEFAULT_BATCH_SIZE)
        _encoder_fingerprint = fingerprint
    return _encoder


def _get_onnx():
    encoder = _get_encoder()
    return encoder.session, encoder.tokenizer


def encode_text(text: str) -> np.ndarray:
    if not isinstance(text, str):
        raise ValueError("Запрос должен быть строкой")
    return _get_encoder().encode_query(text)


def _cache_path(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> Path:
    fingerprint = {"model": model_fingerprint(), "batch_size": batch_size, "texts": texts}
    digest = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return CACHE_DIR / f"embeddings_{len(texts)}_{digest}.npy"


def encode_batch(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> np.ndarray:
    if not isinstance(texts, (list, tuple)) or any(not isinstance(text, str) for text in texts):
        raise ValueError("Описания должны быть списком строк")
    _positive_integer(batch_size, "Размер пакета")
    texts = list(texts)
    if not texts:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    cache_path = _cache_path(texts, batch_size)
    if cache_path.is_file():
        try:
            return _vectors(np.load(cache_path, allow_pickle=False), len(texts))
        except (OSError, EOFError, ValueError):
            cache_path.unlink()
    vectors = _get_encoder().encode(texts, batch_size)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, prefix="embeddings-", suffix=".npy", delete=False) as handle:
            temporary = Path(handle.name)
            np.save(handle, vectors, allow_pickle=False)
        temporary.replace(cache_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return vectors


def to_bytes(embedding: np.ndarray) -> bytes:
    return np.ascontiguousarray(embedding, dtype=np.float32).tobytes()
