"""Подготовка закреплённой ONNX-модели отдельно от лёгкого пути поиска."""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from cf.config import ONNX_DIR


def model_spec() -> dict:
    from importlib.resources import files

    return json.loads((files('cf') / 'data/model.json').read_text(encoding='utf-8'))


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def verify_artifact(directory: Path) -> dict:
    spec = model_spec()
    try:
        metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RuntimeError('Нет корректных метаданных модели. Выполните cf --prepare-model.') from error
    if not isinstance(metadata, dict):
        raise RuntimeError('Метаданные модели должны содержать JSON-объект.')
    for key in ('model', 'revision', 'dimensions', 'max_tokens', 'precision', 'query_prefix',
                'document_prefix', 'normalization', 'pooling_in_graph'):
        if metadata.get(key) != spec[key]:
            raise RuntimeError(f'Модель несовместима: поле {key} не соответствует рабочей конфигурации.')
    from cf.embeddings import _load_metadata
    _load_metadata(directory)
    try:
        actual = {name: file_hash(directory / name) for name in ('model.onnx', 'tokenizer.json')}
    except OSError as error:
        raise RuntimeError('Не найдены все файлы модели. Выполните cf --prepare-model.') from error
    checksum_path = directory / 'checksums.json'
    try:
        expected = (json.loads(checksum_path.read_text(encoding='utf-8'))
                    if checksum_path.is_file() else spec['reference_artifact'])
    except (OSError, ValueError) as error:
        raise RuntimeError('Повреждён файл контрольных сумм модели.') from error
    if not isinstance(expected, dict):
        raise RuntimeError('Файл контрольных сумм модели должен содержать JSON-объект.')
    if any(expected.get(name) != digest for name, digest in actual.items()):
        raise RuntimeError('Контрольные суммы модели не совпадают. Повторите cf --prepare-model --force.')
    return metadata


def _check_vectors(directory: Path) -> None:
    import numpy as np
    from cf.embeddings import OnnxEncoder

    encoder = OnnxEncoder(directory)
    vectors = encoder.encode_batch(['покажи скрытые файлы', 'show hidden files'])
    if vectors.shape != (2, model_spec()['dimensions']) or not np.isfinite(vectors).all():
        raise RuntimeError('Модель вернула некорректные векторы при подготовке.')
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=2e-5):
        raise RuntimeError('Выходные векторы модели не нормализованы.')


def _build(directory: Path, cache: Path) -> None:
    missing = [name for name in ('torch', 'sentence_transformers', 'onnx')
               if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            'Для однократной сборки модели установите зависимости: '
            'python -m pip install ".[export]". Для Linux используйте CPU-сборку PyTorch, '
            'как описано в README. Затем выполните cf --prepare-model. '
            'Готовую модель можно импортировать через --model-source КАТАЛОГ.'
        )
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    os.environ['HF_HOME'] = str(cache)
    os.environ['HF_HUB_CACHE'] = str(cache / 'hub')
    os.environ['SENTENCE_TRANSFORMERS_HOME'] = str(cache)
    os.environ['TORCH_HOME'] = str(cache / 'torch')
    os.environ['XDG_CACHE_HOME'] = str(cache / 'xdg')
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    import numpy as np
    import onnx
    import torch
    from sentence_transformers import SentenceTransformer
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.shape_inference import quant_pre_process

    from cf.config import NUM_THREADS
    spec = model_spec()
    torch.set_num_threads(NUM_THREADS)
    model = SentenceTransformer(
        spec['model'], revision=spec['revision'], device='cpu', cache_folder=str(cache),
        trust_remote_code=False, model_kwargs={'attn_implementation': 'eager'},
    )
    if getattr(model[0].auto_model.config, '_commit_hash', None) != spec['revision']:
        raise RuntimeError('Загруженная ревизия весов отличается от закреплённой.')
    model.max_seq_length = spec['max_tokens']
    model.eval()
    model[0].auto_model.config.use_cache = False
    dummy = model.tokenize(['покажи скрытые файлы', 'show files and their sizes'])
    names = [name for name in ('input_ids', 'attention_mask', 'token_type_ids') if name in dummy]

    class Encoder(torch.nn.Module):
        def __init__(self, pipeline):
            super().__init__()
            self.pipeline = pipeline

        def forward(self, input_ids, attention_mask, token_type_ids=None):
            features = {'input_ids': input_ids, 'attention_mask': attention_mask}
            if token_type_ids is not None:
                features['token_type_ids'] = token_type_ids
            vectors = self.pipeline(features)['sentence_embedding']
            return torch.nn.functional.normalize(vectors, p=2, dim=1)

    floating = cache / 'model.fp32.onnx'
    with torch.inference_mode():
        torch.onnx.export(
            Encoder(model).eval(), tuple(dummy[name] for name in names), str(floating),
            input_names=names, output_names=['sentence_embedding'],
            dynamic_axes={**{name: {0: 'batch', 1: 'sequence'} for name in names},
                          'sentence_embedding': {0: 'batch'}},
            opset_version=spec['opset'], dynamo=False,
        )
    preprocessed = cache / 'model.preprocessed.onnx'
    quant_pre_process(str(floating), str(preprocessed), skip_optimization=True, skip_symbolic_shape=True)
    graph = directory / 'model.onnx'
    quantize_dynamic(str(preprocessed), str(graph), weight_type=QuantType.QUInt8,
                     per_channel=True, reduce_range=False,
                     op_types_to_quantize=spec['quantization']['op_types'],
                     extra_options={'MatMulConstBOnly': True})
    raw = onnx.load(str(graph), load_external_data=False)
    if any(value.data_location == onnx.TensorProto.EXTERNAL for value in raw.graph.initializer):
        raise RuntimeError('Экспорт создал внешние веса вместо самостоятельного ONNX-файла.')
    onnx.checker.check_model(raw)
    if not any(node.op_type == 'MatMulInteger' for node in raw.graph.node):
        raise RuntimeError('В экспортированной модели нет 8-битных матричных операций.')
    model.tokenizer.save_pretrained(str(directory))
    metadata = {key: value for key, value in spec.items()
                if key not in ('reference_artifact', 'license', 'source')}
    metadata.update(
        input_names=names, output_name='sentence_embedding', model_file='model.onnx',
        pad_token_id=model.tokenizer.pad_token_id, pad_token=model.tokenizer.pad_token,
        padding_side=model.tokenizer.padding_side, truncation_side=model.tokenizer.truncation_side,
        do_lower_case=bool(getattr(model[0], 'do_lower_case', False)), strip_text=True,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    _write_json(directory / 'metadata.json', metadata)
    _write_json(directory / 'checksums.json', {name: file_hash(directory / name)
                                             for name in ('model.onnx', 'tokenizer.json')})
    from cf.embeddings import OnnxEncoder
    runtime = OnnxEncoder(directory)
    probes = ['покажи скрытые файлы', 'не завершай процесс, а приостанови его',
              'show hidden files', ' '.join(['длинный текст'] * 100)]
    reference = model.encode(probes, normalize_embeddings=True, convert_to_numpy=True,
                             show_progress_bar=False, batch_size=4)
    actual = runtime.encode_batch(probes, batch_size=4)
    cosine = np.sum(reference * actual, axis=1)
    if not np.isfinite(actual).all() or float(cosine.min()) < 0.97:
        raise RuntimeError('8-битный экспорт не прошёл проверку численной точности.')


def prepare_model(force: bool = False, source: str | Path | None = None) -> Path:
    target = Path(ONNX_DIR)
    if source is None and not force:
        try:
            verify_artifact(target)
            return target
        except (RuntimeError, OSError, ValueError):
            pass
    source_path = Path(source).expanduser().absolute() if source is not None else None
    if source_path is not None:
        verify_artifact(source_path)
        if source_path == target.absolute():
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cf-model-', dir=target.parent) as work:
        work = Path(work)
        staged = work / 'ready'
        if target.is_dir():
            shutil.copytree(target, staged)
        else:
            staged.mkdir()
        if source_path is not None:
            for path in source_path.iterdir():
                if path.is_file() and path.suffix in ('.json', '.txt', '.onnx'):
                    shutil.copyfile(path, staged / path.name)
            _write_json(staged / 'checksums.json', {name: file_hash(staged / name)
                                                  for name in ('model.onnx', 'tokenizer.json')})
        else:
            cache = work / 'cache'
            cache.mkdir()
            _build(staged, cache)
        verify_artifact(staged)
        _check_vectors(staged)
        backup = target.with_name(target.name + '.backup-' + uuid.uuid4().hex[:8])
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return target
