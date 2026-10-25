"""P3-01 GEO 清单解析的离线测试（不联网）。

关键约束：文件名里出现 Velocyto / spliced 只能作为「最低等级证据」，
绝不等于文件内部真的有 spliced/unspliced 层。
"""
import unittest

from cellflow import publicdata


# 真实的 NCBI Apache 目录页片段（2026-10-26 实测 GSE72857）
REAL_INDEX = '''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html>
 <head><title>Index of /geo/series/GSE72nnn/GSE72857/suppl</title></head>
 <body>
<h1>Index of /geo/series/GSE72nnn/GSE72857/suppl</h1>
<pre>Name                                Last modified      Size  <hr><a href="/geo/series/GSE72nnn/GSE72857/">Parent Directory</a>                                         -   
<a href="GSE72857_experimental_design.txt.gz">GSE72857_experimental_design.txt.gz</a> 2015-11-19 12:06  113K  
<a href="GSE72857_umitab.txt.gz">GSE72857_umitab.txt.gz</a>              2015-11-19 12:06   16M  
<hr></pre>
<a href="https://www.hhs.gov/vulnerability-disclosure-policy/index.html">HHS</a>
</body></html>'''


class Stub(unittest.TestCase):
    def test_series_stub(self):
        self.assertEqual(publicdata.geo_stub('GSE72857'), 'GSE72nnn')
        self.assertEqual(publicdata.geo_stub('gse84133'), 'GSE84nnn')

    def test_sample_stub(self):
        # 实测：/samples/GSM709nnn/GSM7093919/suppl/ 是 404，
        # /samples/GSM7093nnn/.../suppl/ 才是 200。分片规则是「保留除末 3 位外的数字」。
        self.assertEqual(publicdata.geo_stub('GSM7093919'), 'GSM7093nnn')

    def test_short_accession_rejected(self):
        with self.assertRaises(publicdata.PublicDataError):
            publicdata.geo_stub('GSE1')

    def test_dir_urls(self):
        self.assertEqual(publicdata.geo_supplementary_url('GSE72857'),
                         'https://ftp.ncbi.nlm.nih.gov/geo/series/GSE72nnn/GSE72857/suppl/')
        self.assertEqual(publicdata.geo_sample_url('GSM7093919'),
                         'https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM7093nnn/GSM7093919/suppl/')


class SizeParsing(unittest.TestCase):
    def test_human_sizes(self):
        self.assertEqual(publicdata._size_to_bytes('113K'), 113 * 1024)
        self.assertEqual(publicdata._size_to_bytes('16M'), 16 * 1024 ** 2)
        self.assertEqual(publicdata._size_to_bytes('1.5G'), int(1.5 * 1024 ** 3))

    def test_unknown_size(self):
        self.assertIsNone(publicdata._size_to_bytes('-'))
        self.assertIsNone(publicdata._size_to_bytes(''))


class IndexParsing(unittest.TestCase):
    def test_parses_real_ncbi_listing(self):
        entries = publicdata._parse_ftp_index(REAL_INDEX)
        names = [entry['name'] for entry in entries]
        self.assertEqual(names, ['GSE72857_experimental_design.txt.gz',
                                 'GSE72857_umitab.txt.gz'])
        self.assertEqual(entries[0]['size_bytes'], 113 * 1024)
        self.assertEqual(entries[1]['size_bytes'], 16 * 1024 ** 2)
        self.assertEqual(entries[1]['modified'], '2015-11-19 12:06')
        self.assertEqual(entries[1]['size_source'], 'ftp_listing')

    def test_parent_directory_is_ignored(self):
        names = [entry['name'] for entry in publicdata._parse_ftp_index(REAL_INDEX)]
        self.assertNotIn('..', names)
        self.assertNotIn('/', ''.join(names))


class FileClassification(unittest.TestCase):
    def test_loom_gz_is_importable_and_only_filename_evidence(self):
        info = publicdata._classify_file('GSM7093919_kdm6bwt.loom.gz')
        self.assertEqual(info['kind'], 'loom')
        self.assertTrue(info['importable'])
        self.assertEqual(info['splice_evidence'], 'filename_only',
                         '文件名叫 loom/velocyto 只是最低证据，不能算已验证')
        self.assertEqual(info['compression'], '.gz')

    def test_loom_and_h5ad_shapes(self):
        self.assertEqual(publicdata._classify_file('a.loom')['kind'], 'loom')
        self.assertEqual(publicdata._classify_file('a.h5ad')['kind'], 'h5ad')
        self.assertEqual(publicdata._classify_file('barcodes.tsv.gz')['kind'], 'table')
        self.assertEqual(publicdata._classify_file('matrix.mtx.gz')['kind'], '10x_mtx')

    def test_plain_counts_table_has_no_splice_evidence(self):
        info = publicdata._classify_file('GSE72857_umitab.txt.gz')
        self.assertEqual(info['splice_evidence'], 'absent')
        self.assertFalse(info['splice_filename_hint'])

    def test_velocyto_in_name_is_not_verified(self):
        info = publicdata._classify_file('GSE274591_velocyto.h5ad')
        self.assertEqual(info['splice_evidence'], 'filename_only')

    def test_pdf_is_not_importable(self):
        self.assertFalse(publicdata._classify_file('readme.pdf')['importable'])


class RecordShape(unittest.TestCase):
    def test_record_has_contract_fields(self):
        record = publicdata._file_record(
            'GSE1_x.h5ad', 'https://example.org/GSE1_x.h5ad', 'series', 'GSE1',
            gse='GSE1', size_bytes=1024, size_text='1K', size_source='ftp_listing')
        for field in ('name', 'url', 'level', 'accession', 'size_bytes', 'size_text',
                      'size_source', 'splice_evidence', 'kind', 'importable',
                      'download_state', 'checksum', 'metadata_source', 'recommended_for'):
            self.assertIn(field, record, f'文件清单缺少字段：{field}')
        self.assertEqual(record['download_state'], 'not_downloaded')
        self.assertEqual(record['level'], 'series')


if __name__ == '__main__':
    unittest.main()
