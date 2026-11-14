import tempfile
from pathlib import Path
import unittest

from benchmarks.onnx_export_worker import graph_info


class ExportGraphTests(unittest.TestCase):
    def test_external_weights_are_rejected_before_loading_them(self):
        import onnx
        from onnx import TensorProto, helper

        weight = TensorProto()
        weight.name = 'weight'
        weight.data_type = TensorProto.FLOAT
        weight.dims.extend([1])
        weight.data_location = TensorProto.EXTERNAL
        location = weight.external_data.add()
        location.key = 'location'
        location.value = 'absent-weights.bin'
        output = helper.make_tensor_value_info('output', TensorProto.FLOAT, [1])
        graph = helper.make_graph([helper.make_node('Identity', ['weight'], ['output'])],
                                  'external-weight-probe', [], [output], [weight])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.onnx'
            path.write_bytes(model.SerializeToString())
            with self.assertRaisesRegex(ValueError, 'self-contained'):
                graph_info(path)


if __name__ == '__main__':
    unittest.main()
