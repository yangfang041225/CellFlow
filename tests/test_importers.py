"""统一导入测试：格式识别 + 矩阵方向自动判断。

覆盖：CSV 两种方向、h5ad 两种方向、10x 三件套、zip 包、loom，以及 data.load 的统一入口。
全部离线，不联网。
"""
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from cellflow import importers
from cellflow.data import load

GENES = ['CD3D', 'INS', 'GCG', 'MT-CO1', 'PTPRC', 'SOX9', 'KRT19', 'CTRB1', 'AMY2A', 'CPA1']
CELLS = ['AAACCTGAGAAACCAT-1', 'AAACCTGAGAAACCCT-1', 'AAACCTGAGAAACCGA-1', 'AAACCTGAGAAACCGG-1']


class Importers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rng = np.random.default_rng(0)
        self.cells_by_genes = rng.poisson(3, size=(len(CELLS), len(GENES))).astype(float)
        self.genes_by_cells = self.cells_by_genes.T

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------ 表格
    def _csv(self, matrix, rows, cols, name):
        path = self.root / name
        pd.DataFrame(matrix, index=rows, columns=cols).to_csv(path)
        return path

    def test_csv_cells_by_genes(self):
        path = self._csv(self.cells_by_genes, CELLS, GENES, 'cells_by_genes.csv')
        result = importers.import_any(path)
        self.assertEqual(result.kind, 'table')
        self.assertFalse(result.transposed)
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertEqual(result.dataset.cells[0], CELLS[0])
        self.assertEqual(result.dataset.genes[0], GENES[0])

    def test_csv_genes_by_cells_is_transposed(self):
        path = self._csv(self.genes_by_cells, GENES, CELLS, 'genes_by_cells.csv')
        result = importers.import_any(path)
        self.assertTrue(result.transposed, '“基因 × 细胞”的表格应当被自动转置')
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertEqual(result.dataset.cells[0], CELLS[0])
        self.assertEqual(result.dataset.genes[0], GENES[0])
        self.assertTrue(any('转置' in note for note in result.notes))

    def test_csv_with_metadata_columns(self):
        path = self.root / 'with_meta.csv'
        frame = pd.DataFrame(self.cells_by_genes, index=CELLS, columns=GENES)
        frame.insert(0, 'assigned_cluster', ['a', 'b', 'a', 'b'])
        frame.to_csv(path)
        result = importers.import_any(path)
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertIn('assigned_cluster', result.dataset.obs)

    # ------------------------------------------------------------ h5ad
    def test_h5ad_both_orientations(self):
        import anndata as ad
        ok = ad.AnnData(X=sparse.csr_matrix(self.cells_by_genes),
                        obs=pd.DataFrame(index=CELLS), var=pd.DataFrame(index=GENES))
        ok.write_h5ad(self.root / 'ok.h5ad')
        flipped = ad.AnnData(X=sparse.csr_matrix(self.genes_by_cells),
                             obs=pd.DataFrame(index=GENES), var=pd.DataFrame(index=CELLS))
        flipped.write_h5ad(self.root / 'flipped.h5ad')

        first = importers.import_any(self.root / 'ok.h5ad')
        self.assertFalse(first.transposed)
        self.assertEqual(first.dataset.X.shape, (len(CELLS), len(GENES)))

        second = importers.import_any(self.root / 'flipped.h5ad')
        self.assertTrue(second.transposed)
        self.assertEqual(second.dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertEqual(second.dataset.genes[0], GENES[0])

    # ------------------------------------------------------------ 10x / 压缩包 / loom
    def _tenx_dir(self):
        from scipy.io import mmwrite
        folder = self.root / 'filtered_feature_bc_matrix'
        folder.mkdir(exist_ok=True)
        mmwrite(folder / 'matrix.mtx', sparse.coo_matrix(self.genes_by_cells))  # genes × cells
        (folder / 'barcodes.tsv').write_text('\n'.join(CELLS) + '\n', encoding='utf-8')
        (folder / 'features.tsv').write_text(
            '\n'.join(f'ENSG{i:011d}\t{g}\tGene Expression' for i, g in enumerate(GENES)) + '\n',
            encoding='utf-8')
        return folder

    def test_10x_missing_features_is_explained(self):
        folder = self._tenx_dir()
        (folder / 'features.tsv').unlink()
        with self.assertRaises(ValueError) as ctx:
            importers.import_any(folder)
        self.assertIn('features', str(ctx.exception))

    def test_10x_barcode_count_mismatch_is_refused(self):
        folder = self._tenx_dir()
        (folder / 'barcodes.tsv').write_text('\n'.join(CELLS[:2]) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError) as ctx:
            importers.import_any(folder)
        self.assertIn('barcodes', str(ctx.exception))

    def test_10x_missing_barcodes_uses_placeholders_with_note(self):
        folder = self._tenx_dir()
        (folder / 'barcodes.tsv').unlink()
        result = importers.import_any(folder)
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertTrue(any('占位' in note for note in result.notes), result.notes)

    def test_csv_import_reports_memory_estimate(self):
        from scipy import sparse as sp
        import pandas as pd
        path = self.root / 'tiny.csv'
        pd.DataFrame(sp.csr_matrix(self.cells_by_genes).toarray(),
                     index=CELLS, columns=GENES).to_csv(path)
        result = importers.import_any(path)
        self.assertTrue(any('内存检查' in note for note in result.notes), result.notes)

    def test_streaming_csv_matches_plain_read(self):
        import numpy as np
        import pandas as pd
        cells = [f'cell_{i}' for i in range(120)]
        genes = [f'GENE{i}' for i in range(40)]
        rng = np.random.default_rng(3)
        frame = pd.DataFrame(rng.integers(0, 5, size=(120, 40)), index=cells, columns=genes)
        frame.insert(0, 'batch', ['A' if i % 2 else 'B' for i in range(120)])
        path = self.root / 'stream.csv'
        frame.to_csv(path)
        streamed = importers._from_table_streaming(path, ',', chunk_rows=29)
        plain = importers.from_table(path)
        self.assertEqual(streamed.dataset.X.shape, (120, 40))
        self.assertTrue(np.allclose(streamed.dataset.X.toarray(), plain.dataset.X.toarray()))
        self.assertIn('batch', streamed.dataset.obs)
        self.assertIn('分块', streamed.notes[0])

    def test_streaming_csv_flipped_orientation(self):
        import numpy as np
        import pandas as pd
        cells = [''.join('ACGT'[(i >> (2 * k)) & 3] for k in range(8)) + '-1'
                 for i in range(80)]
        genes = [f'GENE{i}' for i in range(30)]
        rng = np.random.default_rng(5)
        frame = pd.DataFrame(rng.integers(0, 4, size=(30, 80)), index=genes, columns=cells)
        path = self.root / 'stream_flip.csv'
        frame.to_csv(path)
        streamed = importers._from_table_streaming(path, ',', chunk_rows=17, flipped=True)
        self.assertEqual(streamed.dataset.X.shape, (80, 30))
        self.assertTrue(any('转置' in note for note in streamed.notes), streamed.notes)

    def test_memory_precheck_blocks_import(self):
        from unittest import mock
        from scipy import sparse as sp
        import pandas as pd
        path = self.root / 'tiny.csv'
        pd.DataFrame(sp.csr_matrix(self.cells_by_genes).toarray(),
                     index=CELLS, columns=GENES).to_csv(path)
        with mock.patch('cellflow.memory.precheck',
                        return_value=('预计需要 99 GB 内存，已拒绝加载。', {})):
            with self.assertRaises(ValueError) as ctx:
                importers.import_any(path)
        self.assertIn('拒绝加载', str(ctx.exception))

    def test_10x_negative_values_are_refused(self):
        from scipy.io import mmwrite
        folder = self._tenx_dir()
        mmwrite(folder / 'matrix.mtx', sparse.coo_matrix(self.genes_by_cells - 5))
        with self.assertRaises(ValueError) as ctx:
            importers.import_any(folder)
        self.assertIn('负数', str(ctx.exception))

    def test_10x_non_integer_values_get_warning(self):
        from scipy.io import mmwrite
        folder = self._tenx_dir()
        mmwrite(folder / 'matrix.mtx', sparse.coo_matrix(self.genes_by_cells + 0.5))
        result = importers.import_any(folder)
        self.assertTrue(any('不是整数' in note for note in result.notes), result.notes)

    def test_10x_directory(self):
        folder = self._tenx_dir()
        result = importers.import_any(folder)
        self.assertEqual(result.kind, '10x-mtx')
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))

    def test_zip_containing_10x(self):
        folder = self._tenx_dir()
        archive = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            for item in folder.iterdir():
                zf.write(item, arcname=f'filtered/{item.name}')
        result = importers.import_any(archive)
        self.assertEqual(result.kind, '10x-mtx')
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))

    def test_loom(self):
        try:
            import loompy
        except ImportError:
            self.skipTest('loompy 未安装')
        path = self.root / 'sample.loom'
        loompy.create(str(path), self.genes_by_cells.astype('float32'),
                      {'Gene': GENES}, {'CellID': CELLS})
        result = importers.import_any(path)
        self.assertEqual(result.kind, 'loom')
        self.assertEqual(result.dataset.X.shape, (len(CELLS), len(GENES)))

    # ------------------------------------------------------------ 统一入口
    def test_data_load_dispatch(self):
        path = self._csv(self.genes_by_cells, GENES, CELLS, 'dispatch.csv')
        dataset = load(path)
        self.assertEqual(dataset.X.shape, (len(CELLS), len(GENES)))
        self.assertEqual(dataset.extras['import']['kind'], 'table')

    def test_unsupported_suffix_is_explained(self):
        path = self.root / 'notes.rds'
        path.write_bytes(b'fake')
        with self.assertRaises(ValueError) as ctx:
            importers.import_any(path)
        self.assertIn('.h5ad', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
