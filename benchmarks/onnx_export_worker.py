"""Export one pinned sentence encoder and validate its FP32 and dynamic INT8 graphs."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from benchmarks import runner


PROBES = [
    '',
    'файлы',
    'найди большие файлы в текущем каталоге',
    'покажи изменения, которые ещё не добавлены в git',
    'find large files in the current directory',
    'Show Git status and staged changes',
    '  Найди README.md и файлы с расширением .JSON  ',
    'длинная строка для проверки ограничения длины текста ' * 150,
]


def validate_job(job: dict) -> dict:
    if not isinstance(job, dict) or not isinstance(job.get('profile'), dict):
        raise ValueError('Expected an export job with a model profile')
    profile = job['profile']
    revision = profile.get('revision', '')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Export requires an immutable full model commit SHA')
    if not isinstance(profile.get('dimensions'), int) or profile['dimensions'] < 1:
        raise ValueError('Invalid embedding dimension')
    for key in ('query_prefix', 'document_prefix'):
        if not isinstance(profile.get(key), str):
            raise ValueError(f'Invalid {key}')
    for key in ('threads', 'max_tokens', 'opset'):
        if not isinstance(job.get(key), int) or isinstance(job[key], bool) or job[key] < 1:
            raise ValueError(f'Invalid {key}')
    if job['opset'] < 17:
        raise ValueError('Use ONNX opset 17 or newer')
    for key in ('run_dir', 'hf_home'):
        if not isinstance(job.get(key), str) or not Path(job[key]).is_absolute():
            raise ValueError(f'{key} must be an absolute WSL path')
        runner.wsl_path(job[key])
    return job


def configure_environment(job: dict) -> None:
    hf_home = runner.wsl_path(job['hf_home'])
    paths = {
        'HF_HOME': hf_home, 'HF_HUB_CACHE': hf_home / 'hub',
        'SENTENCE_TRANSFORMERS_HOME': hf_home,
        'TORCH_HOME': hf_home.parent / 'torch', 'XDG_CACHE_HOME': hf_home.parent / 'xdg',
    }
    for name, path in paths.items():
        os.environ[name] = str(runner.wsl_path(path))
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_PROGRESS_BARS': '1',
        'TOKENIZERS_PARALLELISM': 'false', 'CUDA_VISIBLE_DEVICES': '',
        'OMP_NUM_THREADS': str(job['threads']), 'MKL_NUM_THREADS': str(job['threads']),
        'OPENBLAS_NUM_THREADS': str(job['threads']),
    })


def parity(reference, actual) -> dict:
    import numpy as np

    if reference.shape != actual.shape or not np.isfinite(actual).all():
        raise ValueError('Export returned an invalid shape or nonfinite values')
    norms = np.linalg.norm(actual, axis=1)
    if not np.allclose(norms, 1.0, atol=2e-5):
        raise ValueError('Export returned unnormalized vectors')
    cosine = np.sum(reference * actual, axis=1) / (np.linalg.norm(reference, axis=1) * norms)
    return {'vectors': len(actual), 'min_cosine': float(cosine.min()),
            'mean_cosine': float(cosine.mean()),
            'max_absolute_error': float(np.max(np.abs(reference - actual)))}


def graph_info(path: Path) -> dict:
    import onnx

    graph = onnx.load(str(path), load_external_data=False)
    external = [x.name for x in graph.graph.initializer if x.data_location == onnx.TensorProto.EXTERNAL]
    if external:
        raise ValueError('Expected a self-contained graph without external weight files')
    onnx.checker.check_model(graph)
    operators = Counter(node.op_type for node in graph.graph.node)
    return {
        'bytes': path.stat().st_size, 'sha256': runner.file_hash(path),
        'operator_counts': dict(sorted(operators.items())),
        'int8_initializers': sum(x.data_type == onnx.TensorProto.INT8 for x in graph.graph.initializer),
        'uint8_initializers': sum(x.data_type == onnx.TensorProto.UINT8 for x in graph.graph.initializer),
    }


def export_model(job: dict) -> dict:
    validate_job(job)
    configure_environment(job)
    import numpy as np
    import onnxruntime as ort
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.shape_inference import quant_pre_process
    import torch
    from sentence_transformers import SentenceTransformer

    from benchmarks.onnx_encoder import OnnxEncoder

    torch.set_num_threads(job['threads'])
    torch.set_num_interop_threads(1)
    profile = job['profile']
    root = runner.wsl_path(job['run_dir'])
    root.mkdir(parents=True, exist_ok=True)
    for variant in ('fp32', 'int8'):
        directory = runner.wsl_path(root / variant)
        directory.mkdir(exist_ok=True)
    print(f"Loading pinned {job['profile_name']} offline...", flush=True)
    model = SentenceTransformer(
        profile['model'], revision=profile['revision'], device='cpu',
        cache_folder=job['hf_home'], local_files_only=True, trust_remote_code=False,
        config_kwargs=profile.get('config_kwargs', {}),
        model_kwargs={'attn_implementation': 'eager'},
    )
    resolved = getattr(model[0].auto_model.config, '_commit_hash', None)
    if resolved != profile['revision']:
        raise ValueError('Loaded model revision does not match pinned profile')
    if model.get_sentence_embedding_dimension() != profile['dimensions']:
        raise ValueError('Model dimension does not match profile')
    model.max_seq_length = job['max_tokens']
    model.eval()
    model[0].auto_model.config.use_cache = False
    tokenizer = model.tokenizer
    dummy = model.tokenize(['короткий текст', 'find large files and show their sizes'])
    input_names = [name for name in ('input_ids', 'attention_mask', 'token_type_ids') if name in dummy]

    class SentenceEncoder(torch.nn.Module):
        def __init__(self, pipeline):
            super().__init__()
            self.pipeline = pipeline

        def forward(self, input_ids, attention_mask, token_type_ids=None):
            features = {'input_ids': input_ids, 'attention_mask': attention_mask}
            if token_type_ids is not None:
                features['token_type_ids'] = token_type_ids
            vector = self.pipeline(features)['sentence_embedding']
            return torch.nn.functional.normalize(vector, p=2, dim=1)

    wrapper = SentenceEncoder(model).eval()
    fp32_path = root / 'fp32/model.onnx'
    print('Exporting complete sentence encoder FP32...', flush=True)
    started = time.perf_counter()
    with torch.inference_mode():
        torch.onnx.export(
            wrapper, tuple(dummy[name] for name in input_names), str(fp32_path),
            input_names=input_names, output_names=['sentence_embedding'],
            dynamic_axes={**{name: {0: 'batch', 1: 'sequence'} for name in input_names},
                          'sentence_embedding': {0: 'batch'}},
            opset_version=job['opset'], dynamo=False, do_constant_folding=True,
        )
    export_seconds = time.perf_counter() - started
    fp32_graph = graph_info(fp32_path)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = job['threads']
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(str(fp32_path), sess_options=opts, providers=['CPUExecutionProvider'])
    real_inputs = [value.name for value in session.get_inputs()]
    del session
    metadata = {
        'schema_version': 1, 'profile_name': job['profile_name'], 'model': profile['model'],
        'revision': resolved, 'dimensions': profile['dimensions'], 'max_tokens': job['max_tokens'],
        'query_prefix': profile['query_prefix'], 'document_prefix': profile['document_prefix'],
        'input_names': real_inputs, 'output_name': 'sentence_embedding', 'model_file': 'model.onnx',
        'pad_token_id': tokenizer.pad_token_id, 'pad_token': tokenizer.pad_token,
        'padding_side': tokenizer.padding_side, 'truncation_side': tokenizer.truncation_side,
        'do_lower_case': bool(getattr(model[0], 'do_lower_case', False)), 'strip_text': True,
        'normalization': 'l2', 'pooling_in_graph': True, 'opset': job['opset'],
        'pipeline_modules': [{'name': name, 'class': type(module).__name__}
                             for name, module in model.named_children()],
        'config_kwargs': profile.get('config_kwargs', {}), 'attention_implementation': 'eager',
    }
    for variant in ('fp32', 'int8'):
        tokenizer.save_pretrained(str(root / variant))
        runner.write_json(root / variant / 'metadata.json', {**metadata, 'precision': variant})
    quantization = {
        'method': 'dynamic', 'weight_type': 'QUInt8', 'per_channel': True,
        'reduce_range': False, 'op_types': ['MatMul', 'Gemm', 'Gather'], 'MatMulConstBOnly': True,
        'linear_weights': 'uint8', 'embedding_tables': 'uint8',
    }
    for variant in ('fp32', 'int8'):
        path = root / variant / 'metadata.json'
        details = runner.read_json(path)
        details['quantization'] = quantization if variant == 'int8' else None
        runner.write_json(path, details)
    print('Quantizing linear weights and embedding tables: dynamic U8U8, full range, per-channel...', flush=True)
    started = time.perf_counter()
    intermediate = root / 'preprocessed.onnx'
    quant_pre_process(str(fp32_path), str(intermediate), skip_optimization=True, skip_symbolic_shape=True)
    int8_path = root / 'int8/model.onnx'
    quantize_dynamic(
        str(intermediate), str(int8_path), per_channel=True, reduce_range=False,
        weight_type=QuantType.QUInt8, op_types_to_quantize=quantization['op_types'],
        extra_options={'MatMulConstBOnly': True},
    )
    quantize_seconds = time.perf_counter() - started
    int8_graph = graph_info(int8_path)
    if not int8_graph['uint8_initializers'] or not int8_graph['operator_counts'].get('MatMulInteger'):
        raise ValueError('Quantized graph does not contain actual INT8 matrix operations')
    if int8_graph['bytes'] >= fp32_graph['bytes']:
        raise ValueError('INT8 graph unexpectedly did not shrink')
    intermediate.unlink()
    validation = {'fp32': {}, 'int8': {}}
    print('Checking RU/EN parity, variable sequence lengths, batches 1/3/8 and truncation...', flush=True)
    for kind in ('query', 'document'):
        prefix = profile[f'{kind}_prefix']
        reference = model.encode(PROBES, prompt=prefix, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False, batch_size=8)
        np.save(root / f'reference-{kind}.npy', reference, allow_pickle=False)
        for variant in ('fp32', 'int8'):
            encoder = OnnxEncoder(root / variant, threads=job['threads'], batch_size=8)
            checks = {}
            for batch_size in (1, 3, 8):
                if kind == 'query':
                    actual = encoder._encode(PROBES, prefix=prefix, batch_size=batch_size)
                else:
                    actual = encoder.encode_batch(PROBES, batch_size=batch_size)
                check = parity(reference, actual)
                if variant == 'fp32' and (check['min_cosine'] < 0.99999 or check['max_absolute_error'] > 1e-4):
                    raise ValueError(f'FP32 parity failed: {check}')
                if variant == 'int8' and check['min_cosine'] < 0.97:
                    raise ValueError(f'INT8 smoke cosine below 0.97: {check}')
                checks[str(batch_size)] = check
            query_vector = encoder.encode_query(PROBES[2])
            query_reference = model.encode(PROBES[2], prompt=profile['query_prefix'],
                                           normalize_embeddings=True, convert_to_numpy=True)
            query_check = parity(query_reference[None, :], query_vector[None, :])
            if query_check['min_cosine'] < (0.99999 if variant == 'fp32' else 0.97):
                raise ValueError('Single-query API parity failed')
            validation[variant][kind] = {'batches': checks, 'query_api': query_check}
            del encoder
    result = {
        'schema_version': 1, 'status': 'ready', 'profile_name': job['profile_name'],
        'profile': profile, 'resolved_revision': resolved,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'device': 'cpu', 'max_tokens': job['max_tokens'], 'opset': job['opset'],
        'download_allowed': False, 'full_benchmark_run': False,
        'export_seconds': export_seconds, 'quantize_seconds': quantize_seconds,
        'quantization': quantization,
        'fp32': {'directory': str(root / 'fp32'), **fp32_graph},
        'int8': {'directory': str(root / 'int8'), **int8_graph},
        'validation': validation,
        'library_versions': {name: importlib.metadata.version(name) for name in
                             ('torch', 'sentence-transformers', 'transformers', 'onnx', 'onnxruntime', 'tokenizers', 'numpy')},
    }
    runner.write_json(root / 'result.json', result)
    print(f"Ready: {job['profile_name']}, FP32 {fp32_graph['bytes']} bytes, INT8 {int8_graph['bytes']} bytes", flush=True)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    args = parser.parse_args(argv)
    job = runner.read_json(runner.wsl_path(args.job))
    try:
        export_model(job)
        return 0
    except Exception as error:
        import traceback
        traceback.print_exc()
        root = runner.wsl_path(job['run_dir'])
        runner.write_json(root / 'result.json', {'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
