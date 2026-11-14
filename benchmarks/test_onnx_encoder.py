"""Small real ONNX/tokenizer checks without pretrained models."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks.onnx_encoder import OnnxEncoder


AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "onnx", "onnxruntime", "tokenizers"))


@unittest.skipUnless(AVAILABLE, "ONNX runtime test dependencies are not installed")
class OnnxEncoderTests(unittest.TestCase):
    def setUp(self):
        import onnx
        from onnx import TensorProto, helper
        from tokenizers import Tokenizer, models, pre_tokenizers, processors

        self.temporary = tempfile.TemporaryDirectory(prefix="cf-onnx-encoder-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        vocab = {word: index for index, word in enumerate(("[PAD]", "[UNK]", "[CLS]", "[SEP]", "q", "d", "alpha", "beta", "gamma", "delta"))}
        tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.post_processor = processors.TemplateProcessing(
            single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
        )
        tokenizer.save(str(self.directory / "tokenizer.json"))
        names = ["input_ids", "attention_mask", "token_type_ids"]
        inputs = [helper.make_tensor_value_info(name, TensorProto.INT64, ["batch", "sequence"]) for name in names]
        nodes = [
            helper.make_node("Cast", ["input_ids"], ["ids"], to=TensorProto.FLOAT),
            helper.make_node("Cast", ["attention_mask"], ["mask"], to=TensorProto.FLOAT),
            helper.make_node("Cast", ["token_type_ids"], ["types"], to=TensorProto.FLOAT),
            helper.make_node("Mul", ["ids", "mask"], ["masked_ids"]),
            helper.make_node("ReduceSum", ["masked_ids", "axes"], ["ids_sum"], keepdims=1),
            helper.make_node("ReduceSum", ["mask", "axes"], ["length"], keepdims=1),
            helper.make_node("ReduceSum", ["types", "axes"], ["types_sum"], keepdims=1),
            helper.make_node("Concat", ["ids_sum", "length", "types_sum"], ["sentence_embedding"], axis=1),
        ]
        graph = helper.make_graph(
            nodes, "tokenizer-check", inputs,
            [helper.make_tensor_value_info("sentence_embedding", TensorProto.FLOAT, ["batch", 3])],
            initializer=[helper.make_tensor("axes", TensorProto.INT64, [1], [1])],
        )
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
        onnx.checker.check_model(model)
        onnx.save(model, self.directory / "model.onnx")
        self.metadata = {
            "dimensions": 3, "max_tokens": 6,
            "query_prefix": "q ", "document_prefix": "d ",
            "input_names": names, "output_name": "sentence_embedding",
            "pad_token_id": 0, "pad_token": "[PAD]",
            "padding_side": "right", "truncation_side": "right",
        }
        self.save_metadata()

    def save_metadata(self):
        (self.directory / "metadata.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_query_and_document_prefixes_change_tokens(self):
        import numpy as np

        encoder = OnnxEncoder(self.directory)
        np.testing.assert_array_equal(encoder.encode_query("alpha"), [15, 4, 0])
        np.testing.assert_array_equal(encoder.encode_batch(["alpha"]), [[16, 4, 0]])

    def test_padding_batching_and_dynamic_shapes_preserve_vectors(self):
        import numpy as np

        texts = ["alpha", "alpha beta gamma", "", "delta"]
        encoder = OnnxEncoder(self.directory, batch_size=2)
        expected = [[16, 4, 0], [31, 6, 0], [10, 3, 0], [19, 4, 0]]
        batch = encoder.encode_batch(texts)
        self.assertEqual(batch.dtype, np.float32)
        np.testing.assert_array_equal(batch, expected)
        np.testing.assert_array_equal(batch, encoder.encode_batch(texts, batch_size=1))
        np.testing.assert_array_equal(batch, encoder.encode_batch(texts, batch_size=4))
        self.assertEqual(encoder.encode_batch([]).shape, (0, 3))

    def test_truncation_direction_is_taken_from_metadata(self):
        import numpy as np

        text = "alpha beta gamma delta"
        right = OnnxEncoder(self.directory).encode_query(text)
        np.testing.assert_array_equal(right, [30, 6, 0])
        self.metadata["truncation_side"] = "left"
        self.metadata["padding_side"] = "left"
        self.save_metadata()
        encoder = OnnxEncoder(self.directory)
        np.testing.assert_array_equal(encoder.encode_query(text), [35, 6, 0])
        np.testing.assert_array_equal(encoder.encode_batch(["alpha", "alpha beta"]), [[16, 4, 0], [23, 5, 0]])

    def test_sentence_transformer_strip_and_lowercase_options(self):
        import numpy as np

        self.metadata.update(query_prefix="", do_lower_case=True, strip_text=True)
        self.save_metadata()
        np.testing.assert_array_equal(OnnxEncoder(self.directory).encode_query("  ALPHA  "), [11, 3, 0])

    def test_runtime_does_not_import_training_libraries(self):
        program = (
            "import sys; from benchmarks.onnx_encoder import OnnxEncoder; "
            "e=OnnxEncoder(sys.argv[1]); e.encode_query('alpha'); "
            "assert not {'torch','transformers','sentence_transformers'} & sys.modules.keys()"
        )
        result = subprocess.run([sys.executable, "-c", program, str(self.directory)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_graph_metadata_mismatch_fails_before_encoding(self):
        self.metadata["input_names"] = ["input_ids", "attention_mask"]
        self.save_metadata()
        with self.assertRaisesRegex(ValueError, "graph inputs"):
            OnnxEncoder(self.directory)

    def test_invalid_metadata_and_inputs_are_rejected(self):
        for name, value in (("max_tokens", 0), ("dimensions", True), ("pad_token_id", -1),
                            ("padding_side", "middle"), ("do_lower_case", "yes"),
                            ("model_file", "../model.onnx")):
            with self.subTest(name=name):
                original = self.metadata[name] if name in self.metadata else None
                self.metadata[name] = value
                self.save_metadata()
                with self.assertRaises(ValueError):
                    OnnxEncoder(self.directory)
                if original is None:
                    self.metadata.pop(name)
                else:
                    self.metadata[name] = original
        self.save_metadata()
        encoder = OnnxEncoder(self.directory)
        for texts, count in (("alpha", None), ([None], None), (["alpha"], 0)):
            with self.subTest(texts=texts, count=count), self.assertRaises(ValueError):
                encoder.encode_batch(texts, batch_size=count)

    def test_wrong_output_dimensions_are_rejected(self):
        self.metadata["dimensions"] = 2
        self.save_metadata()
        with self.assertRaisesRegex(ValueError, "invalid vectors"):
            OnnxEncoder(self.directory).encode_query("alpha")


if __name__ == "__main__":
    unittest.main()
